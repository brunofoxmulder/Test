from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import aiohttp


CORE_CONVERSATION_URL = "http://supervisor/core/api/conversation/process"
CORE_WEBSOCKET_URL = "ws://supervisor/core/websocket"
PREFERRED_ASSIST_AGENT = "preferred"
PIPELINE_LIST_COMMAND = "assist_pipeline/pipeline/list"
LOGGER = logging.getLogger("maison_elise")


class HomeAssistantConversationError(RuntimeError):
    """Normalized failure while calling Home Assistant Conversation."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class PreferredPipeline:
    pipeline_id: str
    pipeline_name: str
    agent_id: str


@dataclass(slots=True)
class ConversationResult:
    conversation_id: str | None
    continue_conversation: bool
    response_type: str | None
    speech: str | None
    http_status: int
    raw: dict[str, Any]


def _supervisor_token() -> str:
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        raise HomeAssistantConversationError(
            "SUPERVISOR_TOKEN is unavailable",
            code="supervisor_token_unavailable",
        )
    return token


async def resolve_preferred_pipeline(
    *,
    timeout_seconds: float = 5.0,
) -> PreferredPipeline:
    """Resolve the conversation engine of Home Assistant's preferred Assist pipeline."""

    token = _supervisor_token()
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(CORE_WEBSOCKET_URL) as websocket:
                auth_required = await websocket.receive_json()
                if not isinstance(auth_required, dict) or auth_required.get("type") != "auth_required":
                    raise HomeAssistantConversationError(
                        "Unexpected Home Assistant WebSocket authentication handshake",
                        code="ha_ws_protocol_error",
                    )

                await websocket.send_json(
                    {
                        "type": "auth",
                        "access_token": token,
                    }
                )
                auth_result = await websocket.receive_json()
                if not isinstance(auth_result, dict) or auth_result.get("type") != "auth_ok":
                    raise HomeAssistantConversationError(
                        "Home Assistant WebSocket authentication failed",
                        code="ha_ws_auth_failed",
                    )

                await websocket.send_json(
                    {
                        "id": 1,
                        "type": PIPELINE_LIST_COMMAND,
                    }
                )
                response = await websocket.receive_json()
    except HomeAssistantConversationError:
        raise
    except asyncio.TimeoutError as exc:
        raise HomeAssistantConversationError(
            "Home Assistant preferred Assist pipeline lookup timed out",
            code="ha_pipeline_timeout",
        ) from exc
    except (aiohttp.ClientError, TypeError, ValueError) as exc:
        raise HomeAssistantConversationError(
            "Home Assistant preferred Assist pipeline lookup failed",
            code="ha_pipeline_transport_error",
        ) from exc

    if not isinstance(response, dict) or response.get("type") != "result":
        raise HomeAssistantConversationError(
            "Home Assistant returned an invalid pipeline list response",
            code="ha_pipeline_invalid_response",
        )
    if not response.get("success"):
        raise HomeAssistantConversationError(
            "Home Assistant refused the Assist pipeline list request",
            code="ha_pipeline_list_failed",
        )

    result = response.get("result")
    if not isinstance(result, dict):
        raise HomeAssistantConversationError(
            "Home Assistant returned an invalid pipeline list payload",
            code="ha_pipeline_invalid_response",
        )

    preferred_pipeline_id = result.get("preferred_pipeline")
    if not isinstance(preferred_pipeline_id, str) or not preferred_pipeline_id:
        raise HomeAssistantConversationError(
            "Home Assistant has no preferred Assist pipeline",
            code="ha_preferred_pipeline_missing",
        )

    pipelines = result.get("pipelines")
    if not isinstance(pipelines, list):
        raise HomeAssistantConversationError(
            "Home Assistant returned an invalid Assist pipeline collection",
            code="ha_pipeline_invalid_response",
        )

    preferred: dict[str, Any] | None = None
    for pipeline in pipelines:
        if isinstance(pipeline, dict) and pipeline.get("id") == preferred_pipeline_id:
            preferred = pipeline
            break

    if preferred is None:
        raise HomeAssistantConversationError(
            "Preferred Assist pipeline was not present in the pipeline list",
            code="ha_preferred_pipeline_not_found",
        )

    agent_id = preferred.get("conversation_engine")
    if not isinstance(agent_id, str) or not agent_id.startswith("conversation."):
        raise HomeAssistantConversationError(
            "Preferred Assist pipeline has no valid conversation engine",
            code="ha_preferred_pipeline_no_conversation_engine",
        )

    pipeline_name = str(preferred.get("name") or preferred_pipeline_id)
    resolved = PreferredPipeline(
        pipeline_id=preferred_pipeline_id,
        pipeline_name=pipeline_name,
        agent_id=agent_id,
    )
    LOGGER.info(
        "preferred_pipeline_resolved pipeline_id=%s pipeline_name=%s agent_id=%s",
        resolved.pipeline_id,
        resolved.pipeline_name,
        resolved.agent_id,
    )
    return resolved


def build_conversation_payload(
    *,
    text: str,
    agent_id: str,
    language: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Build the minimal request accepted by Home Assistant Conversation."""

    clean_text = text.strip()
    if not clean_text:
        raise ValueError("text must not be empty")

    payload: dict[str, Any] = {
        "text": clean_text,
        "language": language,
        "agent_id": agent_id,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    return payload


def parse_conversation_response(
    body: dict[str, Any],
    *,
    http_status: int,
) -> ConversationResult:
    """Extract only the stable fields needed by Maison Élise."""

    response_obj = body.get("response") or {}
    if not isinstance(response_obj, dict):
        response_obj = {}

    speech_obj = response_obj.get("speech") or {}
    speech: str | None = None

    if isinstance(speech_obj, dict):
        for speech_format in ("plain", "ssml"):
            candidate = speech_obj.get(speech_format)
            if isinstance(candidate, dict) and candidate.get("speech"):
                speech = str(candidate["speech"])
                break

    return ConversationResult(
        conversation_id=(
            str(body["conversation_id"])
            if body.get("conversation_id") is not None
            else None
        ),
        continue_conversation=bool(body.get("continue_conversation", False)),
        response_type=(
            str(response_obj["response_type"])
            if response_obj.get("response_type") is not None
            else None
        ),
        speech=speech,
        http_status=http_status,
        raw=body,
    )


class HomeAssistantConversationClient:
    """Minimal client for Home Assistant's Conversation REST API."""

    def __init__(
        self,
        *,
        agent_id: str,
        language: str = "fr",
        timeout_seconds: float = 45.0,
    ) -> None:
        self._token = _supervisor_token()
        self._agent_id = agent_id
        self._language = language
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def _resolve_agent_id(self) -> str:
        if self._agent_id != PREFERRED_ASSIST_AGENT:
            return self._agent_id
        preferred = await resolve_preferred_pipeline()
        return preferred.agent_id

    async def process(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
    ) -> ConversationResult:
        """Send one text message to the configured HA conversation agent."""

        agent_id = await self._resolve_agent_id()
        payload = build_conversation_payload(
            text=text,
            agent_id=agent_id,
            language=self._language,
            conversation_id=conversation_id,
        )
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    CORE_CONVERSATION_URL,
                    json=payload,
                    headers=headers,
                ) as response:
                    try:
                        body = await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise HomeAssistantConversationError(
                            "Home Assistant returned a non-JSON response",
                            code="ha_non_json_response",
                            http_status=response.status,
                        ) from exc

                    if response.status >= 400:
                        raise HomeAssistantConversationError(
                            "Home Assistant Conversation returned an HTTP error",
                            code="ha_http_error",
                            http_status=response.status,
                        )

                    if not isinstance(body, dict):
                        raise HomeAssistantConversationError(
                            "Home Assistant returned an invalid JSON shape",
                            code="ha_invalid_response",
                            http_status=response.status,
                        )

                    return parse_conversation_response(
                        body,
                        http_status=response.status,
                    )
        except HomeAssistantConversationError:
            raise
        except asyncio.TimeoutError as exc:
            raise HomeAssistantConversationError(
                "Home Assistant Conversation timed out",
                code="ha_timeout",
            ) from exc
        except aiohttp.ClientError as exc:
            raise HomeAssistantConversationError(
                "Home Assistant Conversation transport failed",
                code="ha_transport_error",
            ) from exc
