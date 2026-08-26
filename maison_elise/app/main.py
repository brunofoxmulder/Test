from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from ha_client import (
    HomeAssistantConversationClient,
    HomeAssistantConversationError,
)


OPTIONS_FILE = Path("/data/options.json")
INGRESS_PROXY_IP = ipaddress.ip_address("172.30.32.2")
HOME_ASSISTANT_HOSTNAME = "homeassistant"
BRIDGE_CONVERSATION_PATH = "/api/v1/bridge/conversation"
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
MAX_REQUEST_ID_LENGTH = 128
MAX_SOURCE_LENGTH = 64
MAX_SESSION_ID_LENGTH = 256
MAX_CONVERSATION_ID_LENGTH = 256
MAX_TEXT_LENGTH = 4096

AGENT_ID = "conversation.openai_conversation"
LANGUAGE = "fr"
VERSION = "0.1.0-dev.5"

TESTER_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Maison Élise — Recette locale</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 20px; max-width: 720px; }
    h1 { margin-top: 0; font-size: 1.5rem; }
    .card { border: 1px solid #7777; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    textarea { width: 100%; min-height: 110px; box-sizing: border-box; font: inherit; padding: 10px; }
    button { font: inherit; font-weight: 600; padding: 10px 16px; margin-top: 10px; }
    pre { white-space: pre-wrap; word-break: break-word; min-height: 70px; }
    .muted { opacity: .75; }
  </style>
</head>
<body>
  <h1>Maison Élise — Recette locale</h1>
  <div class="card">
    <div><strong>Version :</strong> 0.1.0-dev.5</div>
    <div><strong>Agent :</strong> conversation.openai_conversation</div>
    <div><strong>Langue :</strong> fr</div>
    <div class="muted">Ingress uniquement · aucune exposition publique Alexa dans ce jalon.</div>
  </div>

  <div class="card">
    <label for="text"><strong>Question de test non actionnante</strong></label>
    <textarea id="text">Réponds uniquement : Maison Élise test réussi.</textarea>
    <button id="send" type="button">Envoyer au Conversation Agent</button>
  </div>

  <div class="card">
    <strong>Résultat</strong>
    <pre id="result">En attente.</pre>
  </div>

<script>
(() => {
  const button = document.getElementById("send");
  const text = document.getElementById("text");
  const result = document.getElementById("result");
  let conversationId = null;

  function requestId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return "ingress-" + Date.now();
  }

  button.addEventListener("click", async () => {
    const question = text.value.trim();
    if (!question) {
      result.textContent = "Question vide.";
      return;
    }

    button.disabled = true;
    result.textContent = "Envoi en cours…";

    const payload = {
      request_id: requestId(),
      text: question,
      source: "recette-ingress",
      session_id: "jalon-a-local",
      conversation_id: conversationId
    };

    try {
      const response = await fetch("./api/v1/conversation", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const body = await response.json();
      if (body.conversation_id) {
        conversationId = body.conversation_id;
      }
      result.textContent = JSON.stringify(body, null, 2);
    } catch (error) {
      result.textContent = "Erreur locale : " + String(error);
    } finally {
      button.disabled = false;
    }
  });
})();
</script>
</body>
</html>
"""


@dataclass(slots=True)
class RequestEnvelope:
    request_id: str
    text: str
    source: str
    session_id: str | None
    conversation_id: str | None


def load_options() -> dict[str, Any]:
    if not OPTIONS_FILE.exists():
        return {}
    return json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))


OPTIONS = load_options()
LOG_LEVEL = str(OPTIONS.get("log_level", "INFO")).upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("maison_elise")


def _required_text_field(
    payload: dict[str, Any],
    field: str,
    *,
    max_length: int,
) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise web.HTTPBadRequest(text=f"Missing {field}")
    if len(value) > max_length:
        raise web.HTTPBadRequest(text=f"{field} is too long")
    if any(ord(char) < 32 for char in value):
        raise web.HTTPBadRequest(text=f"Invalid {field}")
    return value


def _optional_text_field(
    payload: dict[str, Any],
    field: str,
    *,
    max_length: int,
) -> str | None:
    raw_value = payload.get(field)
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    if len(value) > max_length:
        raise web.HTTPBadRequest(text=f"{field} is too long")
    if any(ord(char) < 32 for char in value):
        raise web.HTTPBadRequest(text=f"Invalid {field}")
    return value


def normalize_request(payload: dict[str, Any]) -> RequestEnvelope:
    """Validate the stable V0.1 request contract."""

    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="JSON body must be an object")

    request_id = _required_text_field(
        payload,
        "request_id",
        max_length=MAX_REQUEST_ID_LENGTH,
    )
    source = _required_text_field(
        payload,
        "source",
        max_length=MAX_SOURCE_LENGTH,
    )

    text = str(payload.get("text") or "").strip()
    if not text:
        raise web.HTTPBadRequest(text="Missing text")
    if len(text) > MAX_TEXT_LENGTH:
        raise web.HTTPBadRequest(text="text is too long")

    return RequestEnvelope(
        request_id=request_id,
        text=text,
        source=source,
        session_id=_optional_text_field(
            payload,
            "session_id",
            max_length=MAX_SESSION_ID_LENGTH,
        ),
        conversation_id=_optional_text_field(
            payload,
            "conversation_id",
            max_length=MAX_CONVERSATION_ID_LENGTH,
        ),
    )


def is_ingress_source_allowed(remote: str | None) -> bool:
    """Accept only the Home Assistant Supervisor Ingress proxy."""

    if not remote:
        return False
    try:
        return ipaddress.ip_address(remote) == INGRESS_PROXY_IP
    except ValueError:
        return False


def resolve_home_assistant_ips(hostname: str = HOME_ASSISTANT_HOSTNAME) -> frozenset[IPAddress]:
    """Resolve Home Assistant Core on the private Supervisor network."""

    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError:
        return frozenset()

    addresses: set[IPAddress] = set()
    for _family, _socktype, _proto, _canonname, sockaddr in records:
        try:
            addresses.add(ipaddress.ip_address(sockaddr[0]))
        except (ValueError, IndexError):
            continue
    return frozenset(addresses)


def is_home_assistant_source_allowed(
    remote: str | None,
    allowed_ips: frozenset[IPAddress],
) -> bool:
    """Accept Bridge calls only when they originate from Home Assistant Core."""

    if not remote or not allowed_ips:
        return False
    try:
        return ipaddress.ip_address(remote) in allowed_ips
    except ValueError:
        return False


@web.middleware
async def internal_access_only(request: web.Request, handler):
    if request.path == BRIDGE_CONVERSATION_PATH:
        allowed_ips = request.app["home_assistant_ips"]
        if not is_home_assistant_source_allowed(request.remote, allowed_ips):
            LOGGER.warning(
                "bridge_rejected remote=%s", request.remote or "unknown"
            )
            raise web.HTTPForbidden(text="Home Assistant Core access only")
    elif not is_ingress_source_allowed(request.remote):
        LOGGER.warning("ingress_rejected remote=%s", request.remote or "unknown")
        raise web.HTTPForbidden(text="Ingress access only")
    return await handler(request)


async def tester(_: web.Request) -> web.Response:
    return web.Response(text=TESTER_HTML, content_type="text/html", charset="utf-8")


async def health(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "service": "Maison Élise",
            "version": VERSION,
            "agent_id": AGENT_ID,
            "language": LANGUAGE,
        }
    )


async def conversation(request: web.Request) -> web.Response:
    started = time.monotonic()

    try:
        payload = await request.json()
    except Exception as exc:
        raise web.HTTPBadRequest(text="Invalid JSON") from exc

    envelope = normalize_request(payload)

    if request.path == BRIDGE_CONVERSATION_PATH and envelope.source != "alexa-bridge":
        LOGGER.warning(
            "bridge_rejected_source request_id=%s source=%s",
            envelope.request_id,
            envelope.source,
        )
        raise web.HTTPForbidden(text="Invalid bridge source")

    LOGGER.info(
        "request_received request_id=%s source=%s text_length=%d "
        "has_session_id=%s has_conversation_id=%s agent_id=%s",
        envelope.request_id,
        envelope.source,
        len(envelope.text),
        bool(envelope.session_id),
        bool(envelope.conversation_id),
        AGENT_ID,
    )

    try:
        client = HomeAssistantConversationClient(
            agent_id=AGENT_ID,
            language=LANGUAGE,
        )
        result = await client.process(
            text=envelope.text,
            conversation_id=envelope.conversation_id,
        )
    except HomeAssistantConversationError as exc:
        duration_ms = round((time.monotonic() - started) * 1000)
        LOGGER.error(
            "request_failed request_id=%s source=%s duration_ms=%d "
            "ha_http_status=%s error_code=%s",
            envelope.request_id,
            envelope.source,
            duration_ms,
            exc.http_status if exc.http_status is not None else "none",
            exc.code,
        )
        return web.json_response(
            {
                "ok": False,
                "request_id": envelope.request_id,
                "error": exc.code,
                "upstream_http_status": exc.http_status,
                "duration_ms": duration_ms,
            },
            status=502,
        )
    except ValueError:
        duration_ms = round((time.monotonic() - started) * 1000)
        LOGGER.error(
            "request_failed request_id=%s source=%s duration_ms=%d "
            "ha_http_status=none error_code=invalid_request",
            envelope.request_id,
            envelope.source,
            duration_ms,
        )
        return web.json_response(
            {
                "ok": False,
                "request_id": envelope.request_id,
                "error": "invalid_request",
                "upstream_http_status": None,
                "duration_ms": duration_ms,
            },
            status=400,
        )

    duration_ms = round((time.monotonic() - started) * 1000)
    LOGGER.info(
        "request_completed request_id=%s source=%s duration_ms=%d "
        "ha_http_status=%d response_type=%s has_new_conversation_id=%s "
        "continue_conversation=%s",
        envelope.request_id,
        envelope.source,
        duration_ms,
        result.http_status,
        result.response_type,
        bool(result.conversation_id),
        result.continue_conversation,
    )

    # Dev.5 returns the same normalized response to either the authenticated
    # Ingress tester or the Home Assistant Core Bridge route. Alexa itself is
    # still never exposed directly to this App.
    return web.json_response(
        {
            "ok": True,
            "request_id": envelope.request_id,
            "conversation_id": result.conversation_id,
            "continue_conversation": result.continue_conversation,
            "response_type": result.response_type,
            "speech": result.speech,
            "upstream_http_status": result.http_status,
            "duration_ms": duration_ms,
        }
    )


def create_app() -> web.Application:
    home_assistant_ips = resolve_home_assistant_ips()
    if home_assistant_ips:
        LOGGER.info("bridge_core_source_resolved count=%d", len(home_assistant_ips))
    else:
        LOGGER.warning("bridge_core_source_unresolved")

    app = web.Application(
        middlewares=[internal_access_only],
        client_max_size=64 * 1024,
    )
    app["home_assistant_ips"] = home_assistant_ips
    app.router.add_get("/", tester)
    app.router.add_get("/health", health)
    app.router.add_post("/api/v1/conversation", conversation)
    app.router.add_post(BRIDGE_CONVERSATION_PATH, conversation)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8099"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
