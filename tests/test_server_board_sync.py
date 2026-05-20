import asyncio
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.server_board_sync import (
    BoardSyncManager,
    board_sync_fields_trigger,
    task_is_tracked_for_board_sync,
)
from torque.state import MatrixState


class FakeBoardSyncProvider:
    name = "github"

    def __init__(self):
        self.push_calls = []
        self.preflight_result = {"ok": True, "phase": "preflight"}
        self.preview = {
            "ok": True,
            "phase": "pull_preview",
            "changes": {
                "task": {"local": "Old", "remote": "New"},
                "labels": {"local": ["old"], "remote": ["new"]},
            },
        }
        self.apply_result = {
            "ok": True,
            "phase": "apply_pull",
            "fields": {"task": "New", "labels": ["new"]},
        }
        self.external_items = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_push = False
        self.active = 0
        self.max_active = 0

    async def preflight(self, _settings):
        return dict(self.preflight_result)

    async def push_task(self, task, _settings):
        self.push_calls.append(task.id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.block_push:
                await self.release.wait()
            else:
                await asyncio.sleep(0)
            return {
                **dict(task.board_sync or {}),
                "version": 1,
                "provider": self.name,
                "enabled": True,
                "sync_state": "idle",
                "last_push_at": "2026-05-20T00:00:00+00:00",
                "last_error": "",
            }
        finally:
            self.active -= 1

    async def pull_task(self, _task, _settings):
        return dict(self.preview)

    async def apply_pull(self, _task, _settings, fields):
        result = dict(self.apply_result)
        if "fields" in self.apply_result:
            result["fields"] = {
                key: value
                for key, value in self.apply_result["fields"].items()
                if key in set(fields)
            }
        return result

    async def list_external_items(self, _settings):
        return list(self.external_items)

    async def append_closing_refs(self, pr_body, _linked_issues, _settings=None):
        return pr_body


def make_state(*, auto_track=False):
    state = MatrixState()
    state.add_group("g")
    github_settings = {
        "github_repo": "owner/repo",
        "github_project_owner": "owner",
        "github_project_number": 1,
    }
    if auto_track:
        github_settings["github_sync_default"] = "all"
    state.update_group_settings(
        "g",
        board_sync_provider="github",
        board_sync_enabled=True,
        board_sync_github=github_settings,
    )
    return state


class BoardSyncManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager:
            await manager.stop()

    def make_manager(self, state, provider):
        self.manager = BoardSyncManager(
            state,
            provider_factory=lambda _name: provider,
            debounce_seconds=0,
        )
        return self.manager

    async def test_enqueue_for_trigger_fields_and_create_auto_track(self):
        provider = FakeBoardSyncProvider()
        state = make_state(auto_track=True)
        manager = self.make_manager(state, provider)
        created = state.board_add_task("Created", "g", id="task-create")

        result = manager.enqueue_task(created.id, reason="task_create")

        self.assertTrue(result["queued"])
        self.assertEqual(
            state.board_tasks[created.id].board_sync["sync_state"], "queued")
        for fields in (
            ["provider", "external_id", "external_url"],
            ["task"],
            ["description"],
            ["labels"],
            ["agent_id"],
            ["assigned_engineer_id"],
            ["status"],
            ["lane"],
        ):
            with self.subTest(fields=fields):
                self.assertTrue(board_sync_fields_trigger(fields))
                task = state.board_add_task(
                    "Tracked",
                    "g",
                    id="task-" + fields[0],
                    provider="github",
                    external_id="owner/repo#1",
                )
                enqueued = manager.enqueue_for_local_change(
                    task.id,
                    reason="task_update",
                    fields=fields,
                )
                self.assertTrue(enqueued["queued"])

    async def test_task_level_opt_out_blocks_tracking_and_auto_enqueue(self):
        provider = FakeBoardSyncProvider()
        state = make_state(auto_track=True)
        task = state.board_add_task(
            "Opted out",
            "g",
            id="task-opt-out",
            provider="github",
            external_id="owner/repo#1",
            external_url="https://github.com/owner/repo/issues/1",
            board_sync={
                "version": 1,
                "provider": "github",
                "enabled": False,
                "github": {
                    "issue_number": 1,
                    "issue_url": "https://github.com/owner/repo/issues/1",
                },
            },
        )
        manager = self.make_manager(state, provider)

        self.assertFalse(task_is_tracked_for_board_sync(task))
        auto_result = manager.enqueue_for_local_change(
            task.id,
            reason="task_update",
            fields=["task", "provider", "external_url"],
        )
        explicit_result = manager.enqueue_task(
            task.id,
            reason="group_sync",
            explicit=True,
        )

        self.assertFalse(auto_result["queued"])
        self.assertEqual(auto_result["reason"], "task_opted_out")
        self.assertFalse(explicit_result["queued"])
        self.assertEqual(explicit_result["reason"], "task_opted_out")
        self.assertEqual(state.board_tasks[task.id].board_sync["enabled"], False)
        self.assertEqual(manager.queue.qsize(), 0)

        forced_result = manager.enqueue_task(
            task.id,
            reason="manual_sync",
            explicit=True,
            force=True,
        )
        self.assertTrue(forced_result["queued"])
        self.assertEqual(state.board_tasks[task.id].board_sync["enabled"], True)

    async def test_sync_state_transitions_successfully(self):
        provider = FakeBoardSyncProvider()
        provider.block_push = True
        state = make_state()
        task = state.board_add_task(
            "Tracked",
            "g",
            id="task-1",
            provider="github",
            external_id="owner/repo#1",
            board_sync={"provider": "github", "sync_state": "idle"},
        )
        manager = self.make_manager(state, provider)
        manager.start()

        manager.enqueue_task(task.id, reason="explicit", explicit=True)
        self.assertEqual(state.board_tasks[task.id].board_sync["sync_state"], "queued")
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        self.assertEqual(state.board_tasks[task.id].board_sync["sync_state"], "syncing")
        provider.release.set()
        await asyncio.wait_for(manager.queue.join(), timeout=1)

        sync = state.board_tasks[task.id].board_sync
        self.assertEqual(sync["sync_state"], "idle")
        self.assertEqual(sync["last_error"], "")
        self.assertEqual(sync["last_push_at"], "2026-05-20T00:00:00+00:00")

    async def test_sync_error_state_persists(self):
        class ErrorProvider(FakeBoardSyncProvider):
            async def push_task(self, task, settings):
                await super().push_task(task, settings)
                return {
                    "version": 1,
                    "provider": self.name,
                    "enabled": True,
                    "sync_state": "error",
                    "last_error": "boom",
                }

        provider = ErrorProvider()
        state = make_state()
        task = state.board_add_task(
            "Tracked",
            "g",
            id="task-error",
            provider="github",
            external_id="owner/repo#1",
        )
        manager = self.make_manager(state, provider)
        manager.start()

        manager.enqueue_task(task.id, reason="explicit", explicit=True)
        await asyncio.wait_for(manager.queue.join(), timeout=1)

        self.assertEqual(state.board_tasks[task.id].board_sync["sync_state"], "error")
        self.assertEqual(state.board_tasks[task.id].board_sync["last_error"], "boom")

    async def test_repo_lock_serializes_concurrent_syncs(self):
        provider = FakeBoardSyncProvider()
        provider.block_push = True
        state = make_state()
        first = state.board_add_task(
            "First", "g", id="task-1", provider="github", external_id="owner/repo#1")
        second = state.board_add_task(
            "Second", "g", id="task-2", provider="github", external_id="owner/repo#2")
        manager = self.make_manager(state, provider)

        run_first = asyncio.create_task(manager._sync_one(
            types.SimpleNamespace(task_id=first.id, explicit=False)))
        run_second = asyncio.create_task(manager._sync_one(
            types.SimpleNamespace(task_id=second.id, explicit=False)))
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        self.assertEqual(provider.max_active, 1)
        provider.release.set()
        await asyncio.wait_for(asyncio.gather(run_first, run_second), timeout=1)
        self.assertEqual(provider.max_active, 1)

    async def test_unstarted_manager_leaves_pending_state_visible(self):
        provider = FakeBoardSyncProvider()
        state = make_state()
        task = state.board_add_task(
            "Tracked", "g", id="task-pending", provider="github", external_id="owner/repo#1")
        manager = self.make_manager(state, provider)

        result = manager.enqueue_task(task.id, reason="task_update")

        self.assertTrue(result["queued"])
        self.assertEqual(manager.queue.qsize(), 1)
        self.assertEqual(state.board_tasks[task.id].board_sync["sync_state"], "queued")
        self.assertEqual(provider.push_calls, [])

    async def test_preflight_error_emits_panel_event_and_toast(self):
        provider = FakeBoardSyncProvider()
        provider.preflight_result = {
            "ok": False,
            "phase": "auth",
            "error": "not logged in",
        }
        state = make_state()
        events = []
        toasts = []

        def panel_event(kind, cell_id, agent_name, group, message, task_id=""):
            events.append((kind, group, message, task_id))

        async def toast(message, level="info"):
            toasts.append((level, message))

        self.manager = BoardSyncManager(
            state,
            provider_factory=lambda _name: provider,
            panel_event=panel_event,
            toast=toast,
            debounce_seconds=0,
        )

        result = await self.manager.preflight("g")

        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "auth")
        self.assertEqual(events[0][0], "board_sync_preflight_failed")
        self.assertIn("not logged in", events[0][2])
        self.assertEqual(toasts[0][0], "error")

    async def test_pull_preview_and_apply_round_trip(self):
        provider = FakeBoardSyncProvider()
        state = make_state()
        task = state.board_add_task(
            "Old",
            "g",
            id="task-pull",
            labels=["old"],
            provider="github",
            external_id="owner/repo#1",
            board_sync={"provider": "github", "sync_state": "idle"},
        )
        manager = self.make_manager(state, provider)

        preview = await manager.pull_preview(task.id)
        applied = await manager.pull_apply(task.id, ["task", "labels"])

        self.assertTrue(preview["ok"])
        self.assertIn("task", preview["changes"])
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["applied_fields"], ["labels", "task"])
        refreshed = state.board_tasks[task.id]
        self.assertEqual(refreshed.task, "New")
        self.assertEqual(refreshed.labels, ["new"])
        self.assertEqual(refreshed.board_sync["sync_state"], "idle")
        self.assertIn("last_pull_at", refreshed.board_sync)

    async def test_pull_preview_does_not_mutate_and_apply_selected_fields_only(self):
        provider = FakeBoardSyncProvider()
        provider.preview = {
            "ok": True,
            "phase": "pull_preview",
            "changes": {
                "task": {"local": "Old", "remote": "New"},
                "description": {"local": "Local body", "remote": "Remote body"},
                "labels": {"local": ["old"], "remote": ["new"]},
            },
            "diff": [
                {"field": "task", "local": "Old", "remote": "New"},
                {"field": "description", "local": "Local body", "remote": "Remote body"},
                {"field": "labels", "local": ["old"], "remote": ["new"]},
            ],
        }
        provider.apply_result = {
            "ok": True,
            "phase": "apply_pull",
            "fields": {
                "task": "New",
                "description": "Remote body",
                "labels": ["new"],
            },
        }
        state = make_state()
        task = state.board_add_task(
            "Old",
            "g",
            id="task-selected",
            description="Local body",
            labels=["old"],
            provider="github",
            external_id="owner/repo#1",
            board_sync={"provider": "github", "sync_state": "idle"},
        )
        manager = self.make_manager(state, provider)

        preview = await manager.pull_preview(task.id)
        after_preview = state.board_tasks[task.id]
        self.assertEqual(after_preview.task, "Old")
        self.assertEqual(after_preview.description, "Local body")
        self.assertEqual(after_preview.labels, ["old"])
        applied = await manager.pull_apply(task.id, ["description"])

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["diff"][0]["field"], "task")
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["applied_fields"], ["description"])
        refreshed = state.board_tasks[task.id]
        self.assertEqual(refreshed.task, "Old")
        self.assertEqual(refreshed.description, "Remote body")
        self.assertEqual(refreshed.labels, ["old"])

    async def test_pull_apply_missing_external_issue_surfaces_structured_error(self):
        provider = FakeBoardSyncProvider()
        provider.apply_result = {
            "ok": False,
            "phase": "pull_preview",
            "provider": "github",
            "error": "GraphQL: Could not resolve to an Issue",
            "error_code": "external_not_found",
            "provider_phase": "issue_view",
        }
        state = make_state()
        task = state.board_add_task(
            "Old",
            "g",
            id="task-missing",
            provider="github",
            external_id="owner/repo#404",
            board_sync={"provider": "github", "sync_state": "idle"},
        )
        manager = self.make_manager(state, provider)

        result = await manager.pull_apply(task.id, ["task"])

        self.assertFalse(result["ok"])
        self.assertEqual(result["type"], "board_pull_apply")
        self.assertEqual(result["error_code"], "external_not_found")
        self.assertEqual(state.board_tasks[task.id].task, "Old")

    async def test_import_preview_matches_existing_tasks_and_returns_unlinked(self):
        provider = FakeBoardSyncProvider()
        provider.external_items = [
            {
                "provider": "github",
                "external_id": "owner/repo#1",
                "external_url": "https://github.com/owner/repo/issues/1",
                "title": "Already linked by id",
            },
            {
                "provider": "github",
                "external_id": "owner/repo#2",
                "external_url": "https://github.com/owner/repo/issues/2",
                "title": "Already linked by url",
            },
            {
                "provider": "github",
                "external_id": "owner/repo#3",
                "external_url": "https://github.com/owner/repo/issues/3",
                "title": "Already linked by marker",
                "torque_marker": {"task_id": "task-marker"},
            },
            {
                "provider": "github",
                "external_id": "owner/repo#4",
                "external_url": "https://github.com/owner/repo/issues/4",
                "title": "Import me",
            },
        ]
        state = make_state()
        state.board_add_task(
            "Linked id",
            "g",
            id="task-id",
            provider="github",
            external_id="owner/repo#1",
        )
        state.board_add_task(
            "Linked url",
            "g",
            id="task-url",
            provider="github",
            external_url="https://github.com/owner/repo/issues/2",
        )
        state.board_add_task(
            "Linked marker",
            "g",
            id="task-marker",
        )
        manager = self.make_manager(state, provider)

        result = await manager.import_preview("g")

        self.assertTrue(result["ok"])
        self.assertEqual(result["unlinked_count"], 1)
        self.assertEqual(result["items"][0]["external_id"], "owner/repo#4")
        self.assertEqual(result["matched_count"], 3)
        self.assertEqual(
            {item["matched_by"] for item in result["matched"]},
            {"external_id", "external_url", "torque_marker"},
        )
        self.assertEqual(len(state.board_tasks), 3)
