from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import aiohttp


CORE_CONVERSATION_URL = "http://supervisor/core/api/conversation/process"


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


@dataclass(slots=True)
class ConversationResult:
    conversation_id: str | None
    continue_conversation: bool
    response_type: str | None
    speech: str | None
    http_status: int
    raw: dict[str, Any]


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
        token = os.getenv("SUPERVISOR_TOKEN")
        if not token:
            raise HomeAssistantConversationError(
                "SUPERVISOR_TOKEN is unavailable",
                code="supervisor_token_unavailable",
            )

        self._token = token
        self._agent_id = agent_id
        self._language = language
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def process(
        self,
        *,
        text: str,
        conversation_id: str | None = None,
    ) -> ConversationResult:
        """Send one text message to the configured HA conversation agent."""

        payload = build_conversation_payload(
            text=text,
            agent_id=self._agent_id,
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
