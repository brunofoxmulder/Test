from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PREFERRED_ASSIST_AGENT = "preferred"


@dataclass(frozen=True, slots=True)
class ConversationAgentConfig:
    agent_id: str


def load_conversation_agent_config(options: dict[str, Any]) -> ConversationAgentConfig:
    """Select the Home Assistant preferred Assist pipeline dynamically.

    Since dev.7, Maison Élise no longer follows a provider-specific conversation
    entity stored in App options. Existing ``agent_id`` values are deliberately
    ignored so upgrades immediately stop depending on an obsolete LLM entity.
    """

    del options
    return ConversationAgentConfig(agent_id=PREFERRED_ASSIST_AGENT)
