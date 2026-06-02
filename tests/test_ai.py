import asyncio
import dataclasses
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.ai import LLMClient, LLMFailure, LLMMessage, LLMRequest, LLMResult
from torque.ai_adapters import ADAPTERS
from torque.db import TorqueDB
from torque.state import GlobalSettings


class FakeResponse:
    def __init__(self, payload=None, *, status=200, json_exc=None):
        self.payload = payload if payload is not None else {}
        self.status = status
        self.json_exc = json_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self.json_exc is not None:
            raise self.json_exc
        return self.payload


class FakeSession:
    def __init__(self, responses=None, *, exc=None):
        self.responses = list(responses or [])
        self.exc = exc
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers=None, json=None):
        self.requests.append({
            "url": url,
            "headers": dict(headers or {}),
            "json": json,
        })
        if self.exc is not None:
            raise self.exc
        if not self.responses:
            raise AssertionError("no fake response queued")
        return self.responses.pop(0)


def anthropic_ok(text="hello"):
    return FakeResponse({
        "content": [{"type": "text", "text": text}],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 3,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 7,
        },
        "stop_reason": "end_turn",
    })


def openai_ok(text="hello"):
    return FakeResponse({
        "choices": [{
            "message": {"content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 13, "completion_tokens": 2},
    })


class AITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def run_async(self, coro):
        return asyncio.run(coro)

    def client(self, settings, fake_session=None):
        factory = None
        if fake_session is not None:
            factory = lambda **_kwargs: fake_session
        return LLMClient(settings=settings, db=self.db, session_factory=factory)

    def test_registry_contains_expected_providers(self):
        self.assertEqual(
            set(ADAPTERS),
            {"anthropic", "openai_compatible"},
        )

    def test_typed_failures_disabled_missing_key_missing_model_timeout_invalid_json(self):
        disabled = self.run_async(self.client(GlobalSettings(
            ai_enabled=False,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-test",
        )).complete(LLMRequest(
            purpose="disabled",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertIsInstance(disabled, LLMFailure)
        self.assertEqual(disabled.kind, "disabled")

        missing_model = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="",
        )).complete(LLMRequest(
            purpose="missing_model",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertEqual(missing_model.kind, "missing_model")

        fake_never_called = FakeSession([anthropic_ok()])
        missing_key = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-test",
        ), fake_never_called).complete(LLMRequest(
            purpose="missing_key",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertEqual(missing_key.kind, "missing_key")
        self.assertEqual(fake_never_called.requests, [])

        timeout_session = FakeSession(exc=asyncio.TimeoutError())
        timeout = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="openai_compatible",
            ai_openai_compatible_base_url="http://127.0.0.1:11434/v1",
            ai_openai_compatible_model="local-model",
        ), timeout_session).complete(LLMRequest(
            purpose="timeout",
            messages=[LLMMessage("user", "hi")],
            timeout_seconds=0.01,
        )))
        self.assertEqual(timeout.kind, "timeout")
        self.assertTrue(timeout.retriable)

        invalid_json_session = FakeSession([openai_ok("not json")])
        invalid_json = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="openai_compatible",
            ai_openai_compatible_base_url="http://127.0.0.1:11434/v1",
            ai_openai_compatible_model="local-model",
        ), invalid_json_session).complete_structured(LLMRequest(
            purpose="invalid_json",
            messages=[LLMMessage("user", "json please")],
        )))
        self.assertEqual(invalid_json.kind, "invalid_json")
        payload = invalid_json_session.requests[0]["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_client_resolves_provider_from_settings(self):
        self.db.save_ai_provider_secret("anthropic", "sk-ant-test")
        anthropic_session = FakeSession([anthropic_ok("anthropic text")])
        anthropic = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-test",
        ), anthropic_session).complete(LLMRequest(
            purpose="provider_anthropic",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertIsInstance(anthropic, LLMResult)
        self.assertEqual(anthropic.provider, "anthropic")
        self.assertEqual(anthropic.model, "claude-test")
        self.assertIn("api.anthropic.com", anthropic_session.requests[0]["url"])

        openai_session = FakeSession([openai_ok("openai text")])
        openai = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="openai_compatible",
            ai_openai_compatible_base_url="http://localhost:4321/v1",
            ai_openai_compatible_model="local-model",
        ), openai_session).complete(LLMRequest(
            purpose="provider_openai",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertIsInstance(openai, LLMResult)
        self.assertEqual(openai.provider, "openai_compatible")
        self.assertEqual(openai.model, "local-model")
        self.assertEqual(
            openai_session.requests[0]["url"],
            "http://localhost:4321/v1/chat/completions",
        )

    def test_anthropic_maps_cache_static_prefix_to_ephemeral_content_block(self):
        self.db.save_ai_provider_secret("anthropic", "sk-ant-cache")
        session = FakeSession([anthropic_ok()])
        result = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-cache",
        ), session).complete(LLMRequest(
            purpose="cache_test",
            system="dynamic instructions",
            cache_static_prefix="stable prefix",
            messages=[LLMMessage("user", "summarize")],
        )))
        self.assertIsInstance(result, LLMResult)
        payload = session.requests[0]["json"]
        self.assertEqual(payload["model"], "claude-cache")
        self.assertIsInstance(payload["system"], list)
        self.assertEqual(payload["system"][0]["text"], "stable prefix")
        self.assertEqual(
            payload["system"][0]["cache_control"],
            {"type": "ephemeral"},
        )
        self.assertEqual(result.usage.cache_creation_input_tokens, 5)
        self.assertEqual(result.usage.cache_read_input_tokens, 7)

    def test_openai_compatible_uses_base_url_and_optional_authorization(self):
        no_key_session = FakeSession([openai_ok('{"ok": true}')])
        no_key = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="openai_compatible",
            ai_openai_compatible_base_url="http://local.test:9999/api/v1/",
            ai_openai_compatible_model="local-json",
        ), no_key_session).complete_structured(LLMRequest(
            purpose="openai_no_key",
            messages=[LLMMessage("user", "json")],
        )))
        self.assertIsInstance(no_key, LLMResult)
        self.assertEqual(no_key.structured, {"ok": True})
        request = no_key_session.requests[0]
        self.assertEqual(
            request["url"],
            "http://local.test:9999/api/v1/chat/completions",
        )
        self.assertNotIn("authorization", request["headers"])
        self.assertNotIn("Authorization", request["headers"])

        self.db.save_ai_provider_secret("openai_compatible", "sk-openai-test")
        key_session = FakeSession([openai_ok()])
        keyed = self.run_async(self.client(GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="openai_compatible",
            ai_openai_compatible_base_url="http://local.test:9999/v1",
            ai_openai_compatible_model="local-model",
        ), key_session).complete(LLMRequest(
            purpose="openai_key",
            messages=[LLMMessage("user", "hi")],
        )))
        self.assertIsInstance(keyed, LLMResult)
        self.assertEqual(
            key_session.requests[0]["headers"]["Authorization"],
            "Bearer sk-openai-test",
        )

    def test_configured_key_does_not_leak_on_failure_path_or_metrics(self):
        raw_key = "sk-sensitive-deny-test-abcdef"
        self.db.save_ai_provider_secret("openai_compatible", raw_key)
        session = FakeSession([FakeResponse({"error": {"message": "boom"}}, status=500)])
        logger = logging.getLogger("torque.ai")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            failure = self.run_async(self.client(GlobalSettings(
                ai_enabled=True,
                ai_generation_provider="openai_compatible",
                ai_openai_compatible_base_url="http://local.test:9999/v1",
                ai_openai_compatible_model="local-model",
            ), session).complete(LLMRequest(
                purpose="deny_leak",
                messages=[LLMMessage("user", "prompt must not persist")],
            )))
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertIsInstance(failure, LLMFailure)
        self.assertEqual(failure.kind, "http_error")
        self.assertNotIn(raw_key, json.dumps(dataclasses.asdict(failure)))
        self.assertNotIn(raw_key, stream.getvalue())
        metrics = self.db.list_ai_call_metrics(limit=1)
        self.assertEqual(metrics[0]["purpose"], "deny_leak")
        self.assertEqual(metrics[0]["failure_kind"], "http_error")
        self.assertNotIn(raw_key, json.dumps(metrics, sort_keys=True))
        self.assertNotIn("prompt must not persist", json.dumps(metrics, sort_keys=True))

        columns = {
            row[1]
            for row in self.db._conn.execute(
                "PRAGMA table_info(ai_call_metrics)"
            )
        }
        self.assertEqual(columns, {
            "id",
            "created_at",
            "purpose",
            "provider",
            "model",
            "status",
            "failure_kind",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        })
        lowered = " ".join(sorted(columns)).lower()
        self.assertNotIn("api_key", lowered)
        self.assertNotIn("secret", lowered)
        self.assertNotIn("prompt", lowered)
        self.assertNotIn("response", lowered)


if __name__ == "__main__":
    unittest.main()
