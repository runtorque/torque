import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.ai import LLMFailure, LLMResult, LLMUsage
from torque.ai_summaries import (
    AISummaryService,
    architect_boot_summary_key,
    cached_boot_summary_payload,
    engineer_boot_summary_key,
)
from torque.db import TorqueDB
from torque.state import AgentCell, GlobalSettings, MatrixState


class FakeSummarizer:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.response


class BlockingSummarizer:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        self.started.set()
        await self.release.wait()
        self.finished.set()
        return self.response


class AISummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.state_mod = importlib.import_module("torque.state")
        self._old_data_dir = self.state_mod.DATA_DIR
        self.state_mod.DATA_DIR = self.data_dir
        self.addCleanup(self._restore_data_dir)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.global_settings = GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-summary-test",
        )
        self.architect = AgentCell(
            id="arch-1",
            name="Architect",
            slug="architect",
            group="Torque",
            cell_type="agent",
            kind="architect",
        )
        self.engineer = AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="Torque",
            cell_type="agent",
            kind="engineer",
        )
        self.state.agents[self.architect.id] = self.architect
        self.state.agents[self.engineer.id] = self.engineer
        self.state.groups["Torque"] = [self.architect.id, self.engineer.id]

    def _restore_data_dir(self):
        self.state_mod.DATA_DIR = self._old_data_dir

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_source_hash_invalidation_marks_cached_summary_stale(self):
        key = architect_boot_summary_key(self.architect.id)
        self.state.architect_journal_append(
            self.architect.id,
            "checkpoint",
            "Initial checkpoint: build slice 7.",
        )
        service = AISummaryService(db=self.db, state=self.state)
        initial = service.mark_stale_if_needed(key)
        self.db.ai_upsert_summary({
            **initial,
            "summary_text": "Old summary",
            "status": "ready",
            "generated_at": 100,
        })

        self.state.architect_journal_append(
            self.architect.id,
            "plan",
            "New plan: verify cached boot summary fallback.",
        )
        stale = service.mark_stale_if_needed(key)

        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["summary_text"], "Old summary")
        self.assertNotEqual(stale["source_hash"], initial["source_hash"])

    def test_fresh_refresh_returns_cached_summary_without_provider_call(self):
        key = architect_boot_summary_key(self.architect.id)
        self.state.architect_journal_append(
            self.architect.id,
            "checkpoint",
            "Ready cached checkpoint.",
        )
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="new summary",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
        )
        row = service.mark_stale_if_needed(key)
        self.db.ai_upsert_summary({
            **row,
            "summary_text": "Fresh cached summary",
            "status": "ready",
            "generated_at": 123,
        })

        refreshed = self.run_async(service.refresh(key))

        self.assertEqual(refreshed["status"], "ready")
        self.assertEqual(refreshed["summary_text"], "Fresh cached summary")
        self.assertEqual(fake.calls, [])

    def test_refresh_uses_summarize_and_stores_result(self):
        key = architect_boot_summary_key(self.architect.id)
        self.state.architect_journal_append(
            self.architect.id,
            "checkpoint",
            "Checkpoint: one engineer owns implementation.",
        )
        self.state.save_decision({
            "id": "decision-1",
            "architect_id": self.architect.id,
            "title": "Keep summaries dormant",
            "rationale": "Boot must fall back to raw tools.",
            "status": "accepted",
        })
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Generated architect boot summary",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
        )

        refreshed = self.run_async(service.refresh(key))
        saved = self.db.ai_load_summary(key)

        self.assertEqual(refreshed["status"], "ready")
        self.assertEqual(saved["summary_text"], "Generated architect boot summary")
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("Keep summaries dormant", fake.calls[0]["source_text"])
        self.assertIn("Checkpoint", fake.calls[0]["source_text"])
        self.assertIn("cache_key", fake.calls[0])

    def test_provider_failure_leaves_stale_or_empty_for_raw_fallback(self):
        key = engineer_boot_summary_key(self.engineer.id)
        self.state.journal_append(
            "Torque",
            "checkpoint",
            "Checkpoint: worker is implementing.",
            author_cell_id=self.engineer.id,
        )
        fake = FakeSummarizer(LLMFailure(
            kind="provider_error",
            message="fake provider failed",
            provider="anthropic",
            model="claude-summary-test",
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
        )

        empty = self.run_async(service.refresh(key))
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(empty["summary_text"], "")
        self.assertIn("fake provider failed", empty["error"])

        self.db.ai_upsert_summary({
            **empty,
            "summary_text": "Previous engineer summary",
            "status": "ready",
            "generated_at": 123,
        })
        self.state.journal_append(
            "Torque",
            "plan",
            "Plan: add MCP tests.",
            author_cell_id=self.engineer.id,
        )
        stale = self.run_async(service.refresh(key))

        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["summary_text"], "Previous engineer summary")
        self.assertIn("fake provider failed", stale["error"])

    def test_cached_payload_never_calls_provider_and_ai_disabled_degrades(self):
        key = architect_boot_summary_key(self.architect.id)
        self.db.ai_upsert_summary({
            "summary_key": key,
            "summary_type": "architect_boot",
            "scope_kind": "architect",
            "scope_ref": self.architect.id,
            "provider": "anthropic",
            "model": "claude-summary-test",
            "prompt_version": "boot-summary-v1",
            "source_hash": "hash",
            "source_counts": {"total": 1, "content_hashes": {"a": "b"}},
            "summary_text": "Cached summary",
            "status": "ready",
            "generated_at": 456,
        })
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="should not be called",
            usage=LLMUsage(),
        ))
        self.state.ai_summary_service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
        )

        payload = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["summary"], "Cached summary")
        self.assertNotIn("content_hashes", json.dumps(payload["source_counts"]))
        self.assertEqual(fake.calls, [])

        self.state.global_settings.ai_enabled = False
        disabled = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )
        self.assertEqual(disabled["status"], "empty")
        self.assertEqual(disabled["summary"], "")
        self.assertIn("raw journal/decision", disabled["message"])


class AISummaryMCPToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.state_mod = importlib.import_module("torque.state")
        self._old_data_dir = self.state_mod.DATA_DIR
        self.state_mod.DATA_DIR = self.data_dir
        self.addCleanup(self._restore_data_dir)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.global_settings = GlobalSettings(
            ai_enabled=True,
            ai_generation_provider="anthropic",
            ai_anthropic_model="claude-summary-test",
        )
        self.architect = AgentCell(
            id="arch-1",
            name="Architect",
            slug="architect",
            group="Torque",
            cell_type="agent",
            kind="architect",
            status="running",
        )
        self.engineer = AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="Torque",
            cell_type="agent",
            kind="engineer",
            status="running",
        )
        self.state.agents[self.architect.id] = self.architect
        self.state.agents[self.engineer.id] = self.engineer
        self.state.groups["Torque"] = [self.architect.id, self.engineer.id]
        self.mcp_architect = importlib.reload(
            importlib.import_module("torque.mcp_architect")
        )
        self.mcp_engineer = importlib.reload(
            importlib.import_module("torque.mcp_engineer")
        )

    def _restore_data_dir(self):
        self.state_mod.DATA_DIR = self._old_data_dir

    def _seed_architect_stale_summary(self, service, *, summary_text="Old summary"):
        key = architect_boot_summary_key(self.architect.id)
        if not self.state.architect_journal_read(self.architect.id, limit=1):
            self.state.architect_journal_append(
                self.architect.id,
                "checkpoint",
                "Checkpoint: source material exists.",
            )
        row = service.mark_stale_if_needed(key)
        return self.db.ai_upsert_summary({
            **row,
            "summary_text": summary_text,
            "status": "stale",
            "generated_at": 100,
            "error": "",
        })

    async def _drain_read_tasks(self, service, timeout=1.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while service._read_tasks and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(service._read_tasks)

    async def test_mutation_burst_marks_stale_without_summarize_call(self):
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="must not be generated from mutation path",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )

        for index in range(10):
            self.state.architect_journal_append(
                self.architect.id,
                "checkpoint",
                f"Mutation burst checkpoint {index}.",
            )
            service.schedule_for_delta({
                "op": "architect_journal_append",
                "architect_id": self.architect.id,
            })

        await asyncio.sleep(0.08)
        key = architect_boot_summary_key(self.architect.id)
        saved = self.db.ai_load_summary(key)

        self.assertEqual(fake.calls, [])
        self.assertIsNotNone(saved)
        self.assertIn(saved["status"], {"empty", "stale"})

    async def test_stale_read_schedules_one_background_refresh_and_returns(self):
        fake = BlockingSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Generated later",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        self._seed_architect_stale_summary(service)

        payload = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )

        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["summary"], "Old summary")
        self.assertEqual(fake.calls, [])

        await asyncio.wait_for(fake.started.wait(), timeout=1)
        self.assertEqual(len(fake.calls), 1)
        second = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )
        self.assertIn(second["status"], {"stale", "refreshing"})
        self.assertEqual(len(fake.calls), 1)

        fake.release.set()
        await asyncio.wait_for(fake.finished.wait(), timeout=1)
        await self._drain_read_tasks(service)
        saved = self.db.ai_load_summary(architect_boot_summary_key(self.architect.id))
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["summary_text"], "Generated later")

    async def test_inflight_mutation_keeps_summary_stale_after_provider_returns(self):
        fake = BlockingSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Generated from old source material",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        key = architect_boot_summary_key(self.architect.id)
        self._seed_architect_stale_summary(
            service,
            summary_text="Previous summary",
        )

        cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )
        await asyncio.wait_for(fake.started.wait(), timeout=1)
        refreshing = self.db.ai_load_summary(key)
        self.assertEqual(refreshing["status"], "refreshing")
        self.assertEqual(refreshing["source_counts"]["total"], 1)

        self.state.architect_journal_append(
            self.architect.id,
            "checkpoint",
            "Checkpoint landed while provider refresh was in flight.",
        )
        stale = service.mark_stale_if_needed(key)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["summary_text"], "Previous summary")
        self.assertEqual(stale["source_counts"]["total"], 2)

        fake.release.set()
        await asyncio.wait_for(fake.finished.wait(), timeout=1)
        await self._drain_read_tasks(service)
        saved = self.db.ai_load_summary(key)

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(saved["status"], "stale")
        self.assertEqual(saved["summary_text"], "Previous summary")
        self.assertEqual(saved["source_counts"]["total"], 2)
        self.assertNotEqual(saved["source_hash"], refreshing["source_hash"])
        self.assertIn("changed during", saved["error"])

    async def test_stranded_refreshing_read_downgrades_and_schedules_refresh(self):
        self.state.global_settings.ai_boot_summary_min_interval_seconds = 0
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Recovered from stranded refreshing row",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        key = architect_boot_summary_key(self.architect.id)
        stale = self._seed_architect_stale_summary(
            service,
            summary_text="Interrupted summary",
        )
        self.db.ai_upsert_summary({
            **stale,
            "status": "refreshing",
            "summary_text": "Interrupted summary",
            "error": "",
        })

        payload = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )

        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["summary"], "Interrupted summary")
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.db.ai_load_summary(key)["status"], "stale")

        await self._drain_read_tasks(service)
        saved = self.db.ai_load_summary(key)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(
            saved["summary_text"],
            "Recovered from stranded refreshing row",
        )

    async def test_mark_stale_converts_stranded_refreshing_without_provider(self):
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="should not be called by stale mark",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        key = architect_boot_summary_key(self.architect.id)
        stale = self._seed_architect_stale_summary(
            service,
            summary_text="Interrupted summary",
        )
        self.db.ai_upsert_summary({
            **stale,
            "status": "refreshing",
            "summary_text": "Interrupted summary",
            "error": "",
        })

        marked = service.mark_stale_if_needed(key)

        self.assertEqual(marked["status"], "stale")
        self.assertEqual(marked["summary_text"], "Interrupted summary")
        self.assertIn("interrupted", marked["error"])
        self.assertEqual(fake.calls, [])

    async def test_two_stale_reads_inside_min_interval_make_one_provider_call(self):
        self.state.global_settings.ai_boot_summary_min_interval_seconds = 600
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Generated once",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        self._seed_architect_stale_summary(service)

        cached_boot_summary_payload(self.state, "architect", self.architect.id)
        await self._drain_read_tasks(service)
        self.assertEqual(len(fake.calls), 1)

        key = architect_boot_summary_key(self.architect.id)
        saved = self.db.ai_load_summary(key)
        self.db.ai_upsert_summary({
            **saved,
            "status": "stale",
            "summary_text": "Generated once",
            "error": "",
        })
        cached_boot_summary_payload(self.state, "architect", self.architect.id)
        await asyncio.sleep(0.05)

        self.assertEqual(len(fake.calls), 1)

    async def test_hourly_cap_stops_provider_calls_and_returns_calm_message(self):
        self.state.global_settings.ai_boot_summary_min_interval_seconds = 0
        self.state.global_settings.ai_boot_summary_max_refreshes_per_hour = 1
        fake = FakeSummarizer(LLMResult(
            provider="anthropic",
            model="claude-summary-test",
            text="Generated within cap",
            usage=LLMUsage(),
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        self._seed_architect_stale_summary(service)

        cached_boot_summary_payload(self.state, "architect", self.architect.id)
        await self._drain_read_tasks(service)
        self.assertEqual(len(fake.calls), 1)

        key = architect_boot_summary_key(self.architect.id)
        saved = self.db.ai_load_summary(key)
        self.db.ai_upsert_summary({
            **saved,
            "status": "stale",
            "summary_text": "Generated within cap",
            "error": "",
        })

        capped = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )
        await asyncio.sleep(0.05)

        self.assertEqual(capped["status"], "stale")
        self.assertIn("cost-capped", capped["message"])
        self.assertEqual(len(fake.calls), 1)

    async def test_429_cooldown_returns_stale_note_without_hammering(self):
        self.state.global_settings.ai_boot_summary_min_interval_seconds = 0
        fake = FakeSummarizer(LLMFailure(
            kind="http_error",
            message="Provider request failed with HTTP 429.",
            provider="anthropic",
            model="claude-summary-test",
            retriable=True,
            status_code=429,
            retry_after_seconds=2,
        ))
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=fake,
            debounce_seconds=0.01,
        )
        self.state.ai_summary_service = service
        self._seed_architect_stale_summary(service)

        cached_boot_summary_payload(self.state, "architect", self.architect.id)
        await self._drain_read_tasks(service)
        self.assertEqual(len(fake.calls), 1)

        cooled = cached_boot_summary_payload(
            self.state,
            "architect",
            self.architect.id,
        )
        await asyncio.sleep(0.05)

        self.assertEqual(cooled["status"], "stale")
        self.assertIn("rate-limited", cooled["message"])
        self.assertEqual(len(fake.calls), 1)

    async def test_ai_index_and_recall_service_references_are_untouched(self):
        fake_index_service = object()
        self.state.ai_index_service = fake_index_service
        service = AISummaryService(
            db=self.db,
            state=self.state,
            summarize_func=FakeSummarizer(LLMResult(
                provider="anthropic",
                model="claude-summary-test",
                text="summary",
                usage=LLMUsage(),
            )),
            debounce_seconds=0.01,
        )

        self.state.architect_journal_append(
            self.architect.id,
            "checkpoint",
            "Index/recall services are separate from boot summaries.",
        )
        service.schedule_for_delta({
            "op": "architect_journal_append",
            "architect_id": self.architect.id,
        })
        await asyncio.sleep(0.05)

        self.assertIs(self.state.ai_index_service, fake_index_service)

    async def test_boot_summary_tools_return_cached_rows_without_live_call(self):
        self.db.ai_upsert_summary({
            "summary_key": architect_boot_summary_key(self.architect.id),
            "summary_type": "architect_boot",
            "scope_kind": "architect",
            "scope_ref": self.architect.id,
            "provider": "anthropic",
            "model": "claude-summary-test",
            "prompt_version": "boot-summary-v1",
            "source_hash": "hash-arch",
            "source_counts": {"total": 1},
            "summary_text": "Architect cached summary",
            "status": "ready",
            "generated_at": 100,
        })
        self.db.ai_upsert_summary({
            "summary_key": engineer_boot_summary_key(self.engineer.id),
            "summary_type": "engineer_boot",
            "scope_kind": "engineer",
            "scope_ref": self.engineer.id,
            "provider": "anthropic",
            "model": "claude-summary-test",
            "prompt_version": "boot-summary-v1",
            "source_hash": "hash-eng",
            "source_counts": {"total": 1},
            "summary_text": "Engineer cached summary",
            "status": "ready",
            "generated_at": 101,
        })

        async def forbidden_handle_command(_payload):
            raise AssertionError("boot-summary read tools must not dispatch writes")

        arch_text, arch_error = await self.mcp_architect._dispatch_architect_tool(
            "architect_boot_summary",
            {},
            forbidden_handle_command,
            self.state,
            caller_id=self.architect.id,
        )
        eng_text, eng_error = await self.mcp_engineer._dispatch_engineer_tool(
            "engineer_boot_summary",
            {},
            forbidden_handle_command,
            self.state,
            caller_id=self.engineer.id,
        )

        self.assertFalse(arch_error, arch_text)
        self.assertFalse(eng_error, eng_text)
        self.assertEqual(json.loads(arch_text)["summary"], "Architect cached summary")
        self.assertEqual(json.loads(eng_text)["summary"], "Engineer cached summary")
