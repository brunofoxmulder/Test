from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "maison_elise" / "app"
sys.path.insert(0, str(APP_DIR))

import agent_config  # noqa: E402
import ha_client  # noqa: E402


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeWebSocket:
    def __init__(self, received):
        self._received = list(received)
        self.sent = []

    async def receive_json(self):
        return self._received.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)


class _FakeWebSocketSession:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def ws_connect(self, url):
        self.url = url
        return _AsyncContext(self.websocket)


class _FakeRestResponse:
    status = 200

    async def json(self):
        return {
            "conversation_id": "conversation-1",
            "continue_conversation": False,
            "response": {
                "response_type": "action_done",
                "speech": {"plain": {"speech": "ok"}},
            },
        }


class _FakeRestSession:
    last_post = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, json, headers):
        type(self).last_post = {"url": url, "json": json, "headers": headers}
        return _AsyncContext(_FakeRestResponse())


class PreferredAssistConfigTests(unittest.TestCase):
    def test_stale_provider_agent_id_is_ignored(self):
        config = agent_config.load_conversation_agent_config(
            {"agent_id": "conversation.mistral_old_entity"}
        )
        self.assertEqual(config.agent_id, "preferred")

    def test_missing_agent_id_also_uses_preferred(self):
        config = agent_config.load_conversation_agent_config({})
        self.assertEqual(config.agent_id, "preferred")

    def test_manifest_declares_dev7_and_preferred_mode(self):
        manifest = (REPO_ROOT / "maison_elise" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn('version: "0.1.0-dev.7"', manifest)
        self.assertIn("agent_id: preferred", manifest)

    def test_translation_explains_starred_assist_selection(self):
        translation = (
            REPO_ROOT / "maison_elise" / "translations" / "fr.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("assistant préféré", translation)
        self.assertIn("étoile", translation)


class PreferredAssistResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token_patch = patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test-token"})
        self.token_patch.start()

    async def asyncTearDown(self):
        self.token_patch.stop()

    async def test_resolves_conversation_engine_from_starred_pipeline(self):
        websocket = _FakeWebSocket(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "type": "result",
                    "success": True,
                    "result": {
                        "preferred_pipeline": "cloud",
                        "pipelines": [
                            {
                                "id": "local",
                                "name": "Home Assistant",
                                "conversation_engine": "conversation.home_assistant",
                            },
                            {
                                "id": "cloud",
                                "name": "Home Assistant Cloud",
                                "conversation_engine": "conversation.mistral_conversation",
                            },
                        ],
                    },
                },
            ]
        )

        with patch.object(
            ha_client.aiohttp,
            "ClientSession",
            return_value=_FakeWebSocketSession(websocket),
        ):
            agent_id = await ha_client.resolve_preferred_agent_id()

        self.assertEqual(agent_id, "conversation.mistral_conversation")
        self.assertEqual(websocket.sent[0], {"type": "auth", "access_token": "test-token"})
        self.assertEqual(
            websocket.sent[1],
            {"id": 1, "type": "assist_pipeline/pipeline/list"},
        )

    async def test_missing_preferred_pipeline_fails_closed(self):
        websocket = _FakeWebSocket(
            [
                {"type": "auth_required"},
                {"type": "auth_ok"},
                {
                    "type": "result",
                    "success": True,
                    "result": {"preferred_pipeline": "missing", "pipelines": []},
                },
            ]
        )

        with patch.object(
            ha_client.aiohttp,
            "ClientSession",
            return_value=_FakeWebSocketSession(websocket),
        ):
            with self.assertRaises(ha_client.HomeAssistantConversationError) as caught:
                await ha_client.resolve_preferred_agent_id()

        self.assertEqual(caught.exception.code, "ha_preferred_pipeline_error")

    async def test_process_sends_request_to_resolved_preferred_agent(self):
        _FakeRestSession.last_post = None
        client = ha_client.HomeAssistantConversationClient(agent_id="preferred")

        with (
            patch.object(
                ha_client,
                "resolve_preferred_agent_id",
                new=AsyncMock(return_value="conversation.mistral_conversation"),
            ),
            patch.object(ha_client.aiohttp, "ClientSession", _FakeRestSession),
        ):
            result = await client.process(text="Allume la lampe du salon")

        self.assertEqual(result.speech, "ok")
        self.assertIsNotNone(_FakeRestSession.last_post)
        self.assertEqual(
            _FakeRestSession.last_post["json"]["agent_id"],
            "conversation.mistral_conversation",
        )
        self.assertEqual(
            _FakeRestSession.last_post["url"],
            ha_client.CORE_CONVERSATION_URL,
        )


if __name__ == "__main__":
    unittest.main()
