from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_AGENT_ID = "conversation.openai_conversation"
MAX_AGENT_ID_LENGTH = 256


@dataclass(frozen=True, slots=True)
class ConversationAgentConfig:
    agent_id: str


def _validate_agent_id(value: Any) -> str:
    agent_id = str(value).strip()
    if not agent_id:
        raise ValueError("agent_id must not be empty")
    if len(agent_id) > MAX_AGENT_ID_LENGTH:
        raise ValueError("agent_id is too long")
    if any(ord(char) < 32 for char in agent_id):
        raise ValueError("agent_id contains control characters")
    if not agent_id.startswith("conversation."):
        raise ValueError("agent_id must be a Home Assistant conversation entity")
    return agent_id


def load_conversation_agent_config(options: dict[str, Any]) -> ConversationAgentConfig:
    """Load the selected HA conversation agent from App options.

    Missing configuration keeps the dev.5 OpenAI behavior for a safe upgrade.
    An explicitly invalid value fails instead of silently falling back to OpenAI.
    """

    raw_agent_id = options.get("agent_id")
    if raw_agent_id is None:
        agent_id = DEFAULT_AGENT_ID
    else:
        agent_id = _validate_agent_id(raw_agent_id)
    return ConversationAgentConfig(agent_id=agent_id)
