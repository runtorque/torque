import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class MatrixStateCleanupTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_update_global_settings_validates_xterm_scrollback(self):
        state = self.state_mod.MatrixState()
        state.update_global_settings(xterm_scrollback=4096)
        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

        with self.assertRaises(ValueError):
            state.update_global_settings(xterm_scrollback=99)

        self.assertEqual(state.global_settings.xterm_scrollback, 4096)

    def test_remove_agent_expires_orphaned_asks_and_clears_weaver_question(self):
        state = self.state_mod.MatrixState()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id=agent.id,
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["loom:human", "loom:derived"],
            parent_task_id=parent.id,
        )

        state.agents[agent.id] = agent
        state.groups["g"] = [agent.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=agent.id
        )
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(
            group="g",
            pending_question="Need review",
            pending_note="FYI: tests are green",
            pending_note_kind="note",
            paused=True,
        )
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask

        state.remove_agent(agent.id)

        self.assertEqual(state.group_settings["g"].weaver_agent_id, "")
        self.assertEqual(state.weaver_settings["g"].pending_question, "")
        self.assertEqual(state.weaver_settings["g"].pending_note, "")
        self.assertEqual(state.weaver_settings["g"].pending_note_kind, "")
        self.assertFalse(state.weaver_settings["g"].paused)
        self.assertEqual(state.board_tasks[parent.id].agent_id, "")
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")
        self.assertTrue(
            any(
                msg.get("action") == "system"
                and "source agent is no longer available" in msg.get("message", "")
                for msg in state.board_tasks[ask.id].messages
            )
        )

    def test_cleanup_orphaned_attention_expires_persisted_stale_state(self):
        state = self.state_mod.MatrixState()
        parent = self.state_mod.BoardTask(
            id="task-1",
            task="Parent task",
            group="g",
            lane="In Progress",
            agent_id="missing-agent",
            status="Awaiting Input",
        )
        ask = self.state_mod.BoardTask(
            id="ask-1",
            task="Review plan",
            group="g",
            lane="Backlog",
            labels=["loom:human", "loom:derived"],
            parent_task_id=parent.id,
        )

        state.groups["g"] = []
        state.board_tasks[parent.id] = parent
        state.board_tasks[ask.id] = ask
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(
            group="g",
            pending_question="Old question",
            pending_note="Soft question",
            pending_note_kind="question",
            paused=True,
        )

        cleaned = state.cleanup_orphaned_attention(emit=False)

        self.assertEqual(cleaned, {"asks": 1, "weaver_questions": 1})
        self.assertEqual(state.board_tasks[parent.id].status, "")
        self.assertEqual(state.board_tasks[ask.id].lane, "Done")
        self.assertEqual(state.weaver_settings["g"].pending_question, "")
        self.assertEqual(state.weaver_settings["g"].pending_note, "")
        self.assertEqual(state.weaver_settings["g"].pending_note_kind, "")
        self.assertFalse(state.weaver_settings["g"].paused)
        self.assertEqual(state._delta_ops, [])

    def test_cleanup_stale_boundary_successors_clears_merged_refs(self):
        state = self.state_mod.MatrixState()
        boundary = self.state_mod.BoardTask(
            id="task-1",
            task="Boundary task",
            group="g",
            lane="Done",
            worktree_boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "merged",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        queued = self.state_mod.BoardTask(
            id="task-2",
            task="Queued follow-up",
            group="g",
            lane="To Do",
            resume_after_boundary_task_id=boundary.id,
        )

        state.board_tasks[boundary.id] = boundary
        state.board_tasks[queued.id] = queued

        cleaned = state.cleanup_stale_boundary_successors()

        self.assertEqual(cleaned, 1)
        self.assertEqual(
            state.board_tasks[queued.id].resume_after_boundary_task_id, ""
        )

    def test_load_clears_stale_boundary_successors_and_keeps_open_ones(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-open",
                task="Open boundary",
                group="g",
                lane="Done",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "loom/worker",
                    "status": "open",
                    "recorded_at": "2026-04-07T10:00:00+00:00",
                },
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-merged",
                task="Merged boundary",
                group="g",
                lane="Done",
                worktree_boundary={
                    "repo_root": "/repo",
                    "branch": "loom/worker",
                    "status": "merged",
                    "recorded_at": "2026-04-07T11:00:00+00:00",
                },
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-valid",
                task="Valid queued follow-up",
                group="g",
                lane="To Do",
                resume_after_boundary_task_id="task-open",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-stale",
                task="Stale queued follow-up",
                group="g",
                lane="To Do",
                resume_after_boundary_task_id="task-merged",
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertEqual(
            state.board_tasks["task-valid"].resume_after_boundary_task_id,
            "task-open",
        )
        self.assertEqual(
            state.board_tasks["task-stale"].resume_after_boundary_task_id,
            "",
        )

    def test_load_migrates_legacy_archived_label_to_archived_lane(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Old archived task",
                group="g",
                lane="Done",
                labels=["loom:archived", "bug"],
                updated_at="2026-04-07T10:00:00+00:00",
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        task = state.board_tasks["task-1"]
        self.assertEqual(task.lane, "Archived")
        self.assertEqual(task.archived_from_lane, "Done")
        self.assertEqual(task.archived_at, "2026-04-07T10:00:00+00:00")
        self.assertEqual(task.labels, ["bug"])

    def test_load_migrates_legacy_non_done_archives_without_done_semantics(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})
        db.save_board_task(
            self.state_mod.BoardTask(
                id="dep-1",
                task="Legacy archived child",
                group="g",
                lane="In Progress",
                labels=["loom:archived"],
                updated_at="2026-04-07T10:00:00+00:00",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Blocked follow-up",
                group="g",
                lane="Backlog",
                depends_on=["dep-1"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        dep = state.board_tasks["dep-1"]
        self.assertEqual(dep.lane, "Archived")
        self.assertEqual(dep.archived_from_lane, "In Progress")
        self.assertEqual(dep.archived_at, "2026-04-07T10:00:00+00:00")
        self.assertFalse(state.board_deps_met(state.board_tasks["task-1"]))

    def test_load_restores_auto_dispatch_queue_and_busy_agents(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
                created_by_weaver_id="weaver-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-active",
                task="Active work",
                group="g",
                lane="In Progress",
                agent_id="agent-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-queued",
                task="Queued follow-up",
                group="g",
                lane="Backlog",
            )
        )
        db.save_auto_dispatch_queue("g", [
            {
                "task_id": "task-queued",
                "agent_group": "followup",
                "max_concurrent": 1,
                "target_agent_id": "agent-1",
                "weaver_owner_id": "weaver-1",
                "enqueued_at": "2026-04-07T10:00:00+00:00",
            }
        ])

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertTrue(state.agent_is_busy("agent-1"))
        self.assertEqual(
            state.agent_current_task("agent-1").id,
            "task-active",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].task_id,
            "task-queued",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].target_agent_id,
            "agent-1",
        )
        self.assertEqual(
            state.auto_dispatch_queues["g"][0].weaver_owner_id,
            "weaver-1",
        )

    def test_load_restores_kinds_fields_on_agents_and_tasks(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Architect",
                group="g",
                cell_type="agent",
                created_by_weaver_id="weaver-1",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-1",
                task="Plan work",
                group="g",
                lane="Backlog",
            )
        )
        db._conn.execute(
            "UPDATE agents SET kind=?, role=?, owner_engineer_id=?, "
            "hired_by_architect_id=?, persistent=? WHERE id=?",
            ("architect", "lead", "engineer-1", "architect-root", 1, "agent-1"),
        )
        db._conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id=?, "
            "created_by_architect_id=?, suggested_action=? WHERE id=?",
            ("engineer-1", "architect-root", "feature/review", "task-1"),
        )
        db._conn.commit()

        state = self.state_mod.MatrixState(db=db)
        state.load()

        agent = state.agents["agent-1"]
        self.assertEqual(agent.kind, "architect")
        self.assertEqual(agent.role, "lead")
        self.assertEqual(agent.owner_engineer_id, "engineer-1")
        self.assertEqual(agent.hired_by_architect_id, "architect-root")
        self.assertTrue(agent.persistent)

        task = state.board_tasks["task-1"]
        self.assertEqual(task.assigned_engineer_id, "engineer-1")
        self.assertEqual(task.created_by_architect_id, "architect-root")
        self.assertEqual(task.suggested_action, "feature/review")
        self.assertEqual(
            state.agents["agent-1"].created_by_weaver_id,
            "engineer-1",
        )

    def test_agent_visibility_to_weaver_respects_owned_agent_setting(self):
        state = self.state_mod.MatrixState()
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        owned = self.state_mod.AgentCell(
            id="agent-owned",
            name="Owned worker",
            group="g",
            cell_type="agent",
            created_by_weaver_id=weaver.id,
        )
        legacy = self.state_mod.AgentCell(
            id="agent-legacy",
            name="Legacy worker",
            group="g",
            cell_type="agent",
        )
        other_group = self.state_mod.AgentCell(
            id="agent-other",
            name="Other group worker",
            group="other",
            cell_type="agent",
            created_by_weaver_id=weaver.id,
        )
        state.agents = {
            weaver.id: weaver,
            owned.id: owned,
            legacy.id: legacy,
            other_group.id: other_group,
        }
        state.groups["g"] = [weaver.id, owned.id, legacy.id]
        state.groups["other"] = [other_group.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )

        self.assertTrue(state.agent_is_visible_to_weaver(weaver.id, owned.id))
        self.assertTrue(state.agent_is_visible_to_weaver(weaver.id, legacy.id))

        state.update_weaver_settings("g", restrict_to_created_agents=True)

        self.assertTrue(state.agent_is_visible_to_weaver(weaver.id, owned.id))
        self.assertFalse(state.agent_is_visible_to_weaver(weaver.id, legacy.id))
        self.assertFalse(
            state.agent_is_visible_to_weaver(weaver.id, other_group.id)
        )

    def test_board_remove_task_clears_boundary_successor_links(self):
        state = self.state_mod.MatrixState()
        boundary = self.state_mod.BoardTask(
            id="task-1",
            task="Boundary task",
            group="g",
            lane="Done",
            worktree_boundary={
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T10:00:00+00:00",
            },
        )
        queued = self.state_mod.BoardTask(
            id="task-2",
            task="Queued follow-up",
            group="g",
            lane="To Do",
            resume_after_boundary_task_id=boundary.id,
        )

        state.board_tasks[boundary.id] = boundary
        state.board_tasks[queued.id] = queued

        state.board_remove_task(boundary.id)

        self.assertEqual(
            state.board_tasks[queued.id].resume_after_boundary_task_id, ""
        )

    def test_weaver_resume_semantics_preserve_non_blocking_notes(self):
        state = self.state_mod.MatrixState()
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(
            group="g",
            pending_question="Need review",
            pending_note="FYI: branch is ready",
            pending_note_kind="note",
            paused=True,
        )

        state.update_weaver_settings("g", paused=False, pending_question="")

        ws = state.weaver_settings["g"]
        self.assertEqual(ws.pending_question, "")
        self.assertFalse(ws.paused)
        self.assertEqual(ws.pending_note, "FYI: branch is ready")
        self.assertEqual(ws.pending_note_kind, "note")

    def test_weaver_and_group_setting_updates_normalize_new_policy_fields(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []

        state.update_weaver_settings(
            "g",
            autonomy_mode="not-a-real-mode",
            default_worker_concurrency=0,
            wave_size_preference="gigantic",
            same_agent_follow_up_preference="always",
            digest_verbosity="wall-of-text",
            escalation_style="shrug",
            restrict_to_created_agents=1,
        )
        state.update_group_settings(
            "g",
            worktree_merge_cleanup="???",
            worktree_merge_preserve_diff=True,
        )

        ws = state.weaver_settings["g"]
        gs = state.group_settings["g"]
        self.assertEqual(ws.autonomy_mode, "dispatch_when_clear")
        self.assertEqual(ws.default_worker_concurrency, 1)
        self.assertEqual(ws.wave_size_preference, "small")
        self.assertEqual(ws.same_agent_follow_up_preference, "balanced")
        self.assertEqual(ws.digest_verbosity, "balanced")
        self.assertEqual(ws.escalation_style, "note_then_ask")
        self.assertTrue(ws.restrict_to_created_agents)
        self.assertEqual(gs.worktree_merge_cleanup, "keep")
        self.assertTrue(gs.worktree_merge_preserve_diff)

    def test_history_record_dispatch_persists_weaver_worklog_and_survives_reload(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        weaver = self.state_mod.AgentCell(
            id="weaver-1",
            name="Weaver",
            group="g",
            cell_type="agent",
        )
        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            created_by_weaver_id=weaver.id,
        )
        state.agents[weaver.id] = weaver
        state.agents[worker.id] = worker
        state.groups["g"] = [weaver.id, worker.id]
        state.group_settings["g"] = self.state_mod.GroupSettings(
            weaver_agent_id=weaver.id
        )
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(group="g")
        state._db_save_groups()
        state._db_save_group_settings("g")
        state._db_save_agent(weaver)
        state._db_save_agent(worker)
        state.history_record_agent(weaver)
        state.history_record_agent(worker)

        task = state.board_add_task(
            "Ship Worklog tab",
            "g",
            lane="In Progress",
            id="LOOM:1",
            agent_id=worker.id,
        )

        state.history_record_dispatch(
            worker,
            task,
            weaver_group="g",
            weaver_id=weaver.id,
        )

        self.assertEqual(state.weaver_worklog["g"][0]["task_id"], task.id)
        self.assertTrue(state.weaver_worklog["g"][0]["agent_owned"])

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()

        self.assertEqual(reloaded.weaver_worklog["g"][0]["task_id"], task.id)
        self.assertEqual(
            reloaded.to_dict()["weaver_worklog"]["g"][0]["agent_name"],
            "Worker",
        )


class MatrixStateBoardWorkflowTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

    def test_resolve_board_task_id_prefers_alias_over_archived_literal(self):
        state = self._make_state()
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Archived task",
            group="g",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Live task",
            group="g",
            lane="Backlog",
        )
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases[archived.id] = live.id

        self.assertEqual(state.resolve_task_alias("LOOM:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("LOOM:51"), live.id)
        self.assertEqual(state.resolve_board_task_id("LOOM:5"), live.id)

    def test_board_add_task_archived_literal_collision_creates_persisted_alias(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

        state = self.state_mod.MatrixState(db=db)
        state.groups["Loom"] = []
        state._db_save_groups()
        state.task_id_counters["LOOM"] = 51
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Archived original",
            group="Loom",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        state.board_tasks[archived.id] = archived
        state._db_save_task(archived)

        task = state.board_add_task("New live task", "Loom")

        self.assertIsNotNone(task)
        self.assertNotEqual(task.id, "LOOM:51")
        self.assertEqual(len(task.id), 8)
        self.assertEqual(state.task_id_aliases["LOOM:51"], task.id)
        self.assertTrue(db.board_task_exists(task.id))
        self.assertTrue(db.board_task_exists("LOOM:51"))
        self.assertEqual(state.resolve_board_task_id("LOOM:51"), task.id)

    def test_board_update_task_alias_persists_missing_canonical_and_full_delta(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": []}, {"g": "g"})

        state = self.state_mod.MatrixState(db=db)
        state.groups["g"] = []
        archived = self.state_mod.BoardTask(
            id="LOOM:51",
            task="Highlight Events panel header",
            group="g",
            lane="Archived",
            archived_at="2026-04-07T00:00:00+00:00",
        )
        live = self.state_mod.BoardTask(
            id="bcf3a475",
            task="Keep track of which agent moved a task",
            group="g",
            lane="Backlog",
        )
        db.save_board_task(archived)
        db.save_task_id_alias("LOOM:51", live.id)
        state.board_tasks[archived.id] = archived
        state.board_tasks[live.id] = live
        state.task_id_aliases["LOOM:51"] = live.id

        state.board_update_task(
            "LOOM:51",
            description="Architect-written description",
            action_name="feature/implement",
            assigned_engineer_id="eng-1",
        )

        updated = state.board_tasks[live.id]
        self.assertEqual(updated.description, "Architect-written description")
        self.assertEqual(updated.action_name, "feature/implement")
        self.assertEqual(updated.assigned_engineer_id, "eng-1")
        self.assertEqual(state.board_tasks["LOOM:51"].task, archived.task)
        self.assertTrue(db.board_task_exists(live.id))
        row = db._conn.execute(
            "SELECT description, action_name, assigned_engineer_id "
            "FROM board_tasks WHERE id=?",
            (live.id,),
        ).fetchone()
        self.assertEqual(
            row,
            ("Architect-written description", "feature/implement", "eng-1"),
        )
        archived_row = db._conn.execute(
            "SELECT task, description FROM board_tasks WHERE id='LOOM:51'"
        ).fetchone()
        self.assertEqual(archived_row, (archived.task, ""))
        task_ops = [
            op for op in state._delta_ops
            if op.get("op") == "task_upsert" and op.get("id") == live.id
        ]
        self.assertTrue(task_ops)
        full_delta = task_ops[0]
        self.assertEqual(full_delta["description"], "Architect-written description")
        self.assertEqual(full_delta["action_name"], "feature/implement")
        self.assertEqual(full_delta["assigned_engineer_id"], "eng-1")
        self.assertIn("created_at", full_delta)
        self.assertIn("health_details", full_delta)

        reloaded = self.state_mod.MatrixState(db=db)
        reloaded.load()
        self.assertEqual(reloaded.resolve_board_task_id("LOOM:51"), live.id)
        self.assertEqual(
            reloaded.board_tasks[live.id].description,
            "Architect-written description",
        )

    def test_board_add_task_allocates_group_scoped_root_ids(self):
        state = self.state_mod.MatrixState()
        state.groups["Loom Team"] = []
        state.groups["Ops"] = []

        first = state.board_add_task("First task", "Loom Team")
        second = state.board_add_task("Second task", "Loom Team")
        third = state.board_add_task("Ops task", "Ops")

        self.assertEqual(first.id, "LOOM_TEAM:1")
        self.assertEqual(second.id, "LOOM_TEAM:2")
        self.assertEqual(third.id, "OPS:1")

    def test_board_add_task_transliterates_accented_group_names(self):
        state = self.state_mod.MatrixState()
        state.groups["Atlas Público"] = []

        task = state.board_add_task("Localization", "Atlas Público")

        self.assertEqual(task.id, "ATLAS_PUBLICO:1")

    def test_board_add_task_allocates_pipeline_scoped_child_ids_across_groups(self):
        state = self.state_mod.MatrixState()
        state.groups["Loom"] = []
        state.groups["Review Team"] = []

        root = state.board_add_task("Root", "Loom")
        child = state.board_add_task(
            "Implement",
            "Loom",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        cross_group = state.board_add_task(
            "Review",
            "Review Team",
            parent_task_id=child.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
        )

        self.assertEqual(root.id, "LOOM:1")
        self.assertEqual(child.id, "LOOM:1:1")
        self.assertEqual(cross_group.id, "REVIEW_TEAM:1:2")

    def test_started_descendant_handoff_frees_parent_execution_slot(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        child = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-child",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
            agent_id="agent-2",
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(child)
        self.assertTrue(state.task_has_live_handoff_descendants(parent.id))
        self.assertIsNone(state.agent_current_task("agent-1"))
        self.assertFalse(state.agent_is_busy("agent-1"))

    def test_plain_backlog_child_does_not_free_parent_execution_slot(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        child = state.board_add_task(
            "Follow-up draft",
            "g",
            lane="Backlog",
            id="task-child",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(child)
        self.assertFalse(state.task_has_live_handoff_descendants(parent.id))
        self.assertEqual(state.agent_current_task("agent-1").id, parent.id)
        self.assertTrue(state.agent_is_busy("agent-1"))

    def test_agent_pending_weaver_reply_tasks_only_include_open_tasks_for_worker(self):
        state = self._make_state()
        state.board_add_task(
            "Answered thread",
            "g",
            lane="Done",
            id="task-done",
            reply_agent_id="agent-1",
            labels=["loom:weaver-message"],
        )
        pending_old = state.board_add_task(
            "Older thread",
            "g",
            lane="Backlog",
            id="task-old",
            reply_agent_id="agent-1",
            labels=["loom:weaver-message"],
        )
        pending_new = state.board_add_task(
            "Newer thread",
            "g",
            lane="Backlog",
            id="task-new",
            reply_agent_id="agent-1",
            labels=["loom:weaver-message"],
        )
        state.board_add_task(
            "Other worker thread",
            "g",
            lane="Backlog",
            id="task-other",
            reply_agent_id="agent-2",
            labels=["loom:weaver-message"],
        )

        pending = state.agent_pending_weaver_reply_tasks("agent-1")

        self.assertEqual([task.id for task in pending], [pending_old.id, pending_new.id])

    def test_load_restores_pending_weaver_message_from_open_followup_tasks(self):
        from loom.db import LoomDB

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)
        db.save_groups({"g": ["agent-1"]}, {"g": "g"})
        db.save_group_members("g", ["agent-1"])
        db.save_agent(
            self.state_mod.AgentCell(
                id="agent-1",
                name="Worker",
                group="g",
                cell_type="agent",
            )
        )
        db.save_board_task(
            self.state_mod.BoardTask(
                id="task-reply",
                task="Weaver: Check status",
                group="g",
                lane="Backlog",
                reply_agent_id="agent-1",
                labels=["loom:weaver-message"],
            )
        )

        state = self.state_mod.MatrixState(db=db)
        state.load()

        self.assertTrue(state.agents["agent-1"].pending_weaver_message)
        self.assertEqual(
            [task.id for task in state.agent_pending_weaver_reply_tasks("agent-1")],
            ["task-reply"],
        )

    def test_queued_follow_up_becomes_current_task_over_suspended_parent(self):
        state = self._make_state()
        parent = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-parent",
            agent_id="agent-1",
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            parent_task_id="task-parent",
            pipeline_root_id="task-parent",
            pipeline_depth=1,
            agent_id="agent-2",
        )
        follow_up = state.board_add_task(
            "Fix review issues",
            "g",
            lane="To Do",
            id="task-fix",
            parent_task_id="task-review",
            pipeline_root_id="task-parent",
            pipeline_depth=2,
            agent_id="agent-1",
        )
        state.agents["agent-1"] = self.state_mod.AgentCell(
            id="agent-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            current_task_id="task-parent",
        )

        self.assertIsNotNone(parent)
        self.assertIsNotNone(review)
        self.assertIsNotNone(follow_up)
        self.assertEqual(state.agent_current_task("agent-1").id, follow_up.id)
        self.assertTrue(state.agent_is_busy("agent-1"))

    def test_add_group_rejects_prefix_collisions(self):
        state = self.state_mod.MatrixState()

        state.add_group("Foo Bar")
        state.add_group("Foo-Bar")

        self.assertIn("Foo Bar", state.groups)
        self.assertNotIn("Foo-Bar", state.groups)

    def test_board_task_lifecycle_covers_creation_editing_and_cleanup(self):
        state = self._make_state()
        dep = state.board_add_task(
            "Dependency task",
            "g",
            lane="To Do",
            id="dep-1",
            labels=["ready"],
        )
        task = state.board_add_task(
            "Ship feature",
            "g",
            description="Initial description",
            id="task-1",
            labels=["loom:blocked", "keep"],
            depends_on=["dep-1", "missing-task"],
            verification_mode="deploy",
            verification_state="pending",
            verification_notes="Need manual smoke on staging",
            verification_summary={
                "tests_run": "python3 -m unittest",
                "deploy_needed": True,
            },
            artifacts=[{
                "type": "log",
                "title": "build.log",
                "path": "/tmp/build.log",
                "summary": "Compile failed in auth module",
                "prompt": {"mode": "summary"},
            }],
        )

        self.assertIsNotNone(dep)
        self.assertIsNotNone(task)
        self.assertEqual(task.lane, "Backlog")
        self.assertEqual(task.position, 0)
        self.assertEqual(task.lane_entered_at, task.created_at)
        self.assertEqual(task.depends_on, ["dep-1"])
        self.assertEqual(task.artifacts[0]["type"], "log")
        self.assertEqual(task.artifacts[0]["prompt"]["mode"], "summary")
        self.assertEqual(task.verification_mode, "deploy")
        self.assertEqual(task.verification_state, "pending")
        self.assertEqual(
            task.verification_summary["tests_run"],
            "python3 -m unittest",
        )

        original_slug = task.slug
        state.board_update_task(
            task.id,
            task="Ship feature safely",
            description="Updated description",
            labels=["loom:error", "keep"],
            scheduled_at="2026-04-07T10:00:00+00:00",
            verification_state="failed",
            verification_notes="Smoke failed on login redirect",
            verification_summary={
                "tests_run": "python3 -m unittest tests.test_auth",
                "manual_smoke_done": True,
                "deploy_needed": True,
                "deploy_attempted": True,
                "human_validation_pending": "Confirm prod login redirect",
            },
            artifacts=[{
                "type": "snippet",
                "title": "failing example",
                "content": "assert actual == expected",
                "prompt": {"mode": "auto"},
            }],
        )

        updated = state.board_tasks[task.id]
        self.assertEqual(updated.description, "Updated description")
        self.assertEqual(updated.labels, ["loom:error", "keep"])
        self.assertEqual(updated.scheduled_at, "2026-04-07T10:00:00+00:00")
        self.assertEqual(updated.artifacts[0]["type"], "snippet")
        self.assertEqual(updated.artifacts[0]["storage"]["kind"], "inline")
        self.assertEqual(updated.verification_state, "failed")
        self.assertTrue(updated.verification_summary["manual_smoke_done"])
        self.assertEqual(
            updated.verification_summary["human_validation_pending"],
            "Confirm prod login redirect",
        )
        self.assertNotEqual(updated.slug, original_slug)
        self.assertEqual(updated.lane_entered_at, task.created_at)

        updated.lane_entered_at = "2026-04-06T00:30:00+00:00"
        state.board_move_task(task.id, "Done")

        finished = state.board_tasks[task.id]
        self.assertEqual(finished.lane, "Done")
        self.assertEqual(finished.labels, ["keep"])
        self.assertNotEqual(
            finished.lane_entered_at,
            "2026-04-06T00:30:00+00:00",
        )

        state.board_remove_task(dep.id)

        self.assertEqual(state.board_tasks[task.id].depends_on, [])

    def test_dependency_updates_strip_invalid_entries_and_reject_cycles(self):
        state = self._make_state()
        task_a = state.board_add_task("Task A", "g", id="task-a")
        task_b = state.board_add_task("Task B", "g", id="task-b")
        task_c = state.board_add_task("Task C", "g", id="task-c")

        self.assertIsNotNone(task_a)
        self.assertIsNotNone(task_b)
        self.assertIsNotNone(task_c)

        state.board_update_task(
            task_a.id,
            depends_on=[task_a.id, "missing-task", task_b.id],
        )
        self.assertEqual(state.board_tasks[task_a.id].depends_on, [task_b.id])

        state.board_update_task(task_b.id, depends_on=[task_c.id])
        self.assertFalse(state.board_deps_met(state.board_tasks[task_a.id]))
        self.assertEqual(
            [t.id for t in state.board_get_dependents(task_b.id)],
            [task_a.id],
        )

        state.board_update_task(task_c.id, depends_on=[task_a.id])

        self.assertEqual(state.board_tasks[task_c.id].depends_on, [])

        state.board_move_task(task_b.id, "Done")
        self.assertTrue(state.board_deps_met(state.board_tasks[task_a.id]))

    def test_archive_and_restore_preserve_source_lane_and_done_dependencies(self):
        state = self._make_state()
        dep = state.board_add_task("Dependency", "g", id="dep-1")
        task = state.board_add_task(
            "Follow-up",
            "g",
            id="task-1",
            depends_on=["dep-1"],
        )

        self.assertIsNotNone(dep)
        self.assertIsNotNone(task)

        state.board_move_task(dep.id, "Done")
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

        state.board_archive_task(dep.id)

        archived = state.board_tasks[dep.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "Done")
        self.assertTrue(archived.archived_at)
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

        state.board_unarchive_task(dep.id)

        restored = state.board_tasks[dep.id]
        self.assertEqual(restored.lane, "Done")
        self.assertEqual(restored.archived_at, "")
        self.assertEqual(restored.archived_from_lane, "")
        self.assertTrue(state.board_deps_met(state.board_tasks[task.id]))

    def test_archiving_active_task_unlinks_agent_and_clears_busy_state(self):
        state = self._make_state()
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            current_task_id="task-1",
        )
        state.agents[agent.id] = agent
        state.groups["g"] = [agent.id]
        task = state.board_add_task(
            "In-flight work",
            "g",
            lane="In Progress",
            id="task-1",
            agent_id=agent.id,
        )

        self.assertIsNotNone(task)
        self.assertTrue(state.agent_is_busy(agent.id))

        state.board_archive_task(task.id)

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "In Progress")
        self.assertEqual(archived.agent_id, "")
        self.assertFalse(state.agent_is_busy(agent.id))
        self.assertIsNone(state.agent_current_task(agent.id))

    def test_board_update_task_routes_archived_lane_through_archive_semantics(self):
        state = self._make_state()
        task = state.board_add_task("Ship release", "g", lane="Done", id="task-1")

        self.assertIsNotNone(task)

        state.board_update_task(task.id, lane="Archived", description="Keep for reference")

        archived = state.board_tasks[task.id]
        self.assertEqual(archived.lane, "Archived")
        self.assertEqual(archived.archived_from_lane, "Done")
        self.assertEqual(archived.description, "Keep for reference")

    def test_lane_transition_timestamp_tracks_update_and_remove_lane_moves(self):
        state = self._make_state()
        state.board_add_lane("Review")
        task = state.board_add_task(
            "Review the patch",
            "g",
            lane="Backlog",
            id="task-review",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.lane_entered_at, task.created_at)

        original_lane_entered_at = task.lane_entered_at
        state.board_update_task(task.id, lane="Review")

        moved = state.board_tasks[task.id]
        self.assertEqual(moved.lane, "Review")
        self.assertNotEqual(moved.lane_entered_at, original_lane_entered_at)

        before_remove = moved.lane_entered_at
        state.board_remove_lane("Review", move_tasks_to="To Do")

        moved_again = state.board_tasks[task.id]
        self.assertEqual(moved_again.lane, "To Do")
        self.assertNotEqual(moved_again.lane_entered_at, before_remove)

    def test_schedule_crud_tracks_due_items_and_slug_updates(self):
        state = self._make_state()
        due = state.schedule_add(
            "Morning sync",
            "g",
            cron_expr="0 8 * * *",
            next_run_at="2026-04-06T08:00:00+00:00",
            labels=["ops"],
        )
        later = state.schedule_add(
            "Weekly review",
            "g",
            scheduled_at="2026-04-08T08:00:00+00:00",
            next_run_at="2026-04-08T08:00:00+00:00",
        )

        self.assertIsNotNone(due)
        self.assertIsNotNone(later)
        self.assertEqual(
            [s.id for s in state.schedule_get_due("2026-04-06T08:00:00+00:00")],
            [due.id],
        )

        state.schedule_update(due.id, name="Morning standup", enabled=False)

        updated = state.schedules[due.id]
        self.assertEqual(updated.slug, "morning-standup")
        self.assertFalse(updated.enabled)
        self.assertEqual(
            state.schedule_get_due("2026-04-09T08:00:00+00:00"),
            [later],
        )


class MatrixStateWeaverStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state_with_open_stream(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        state.weaver_settings["g"] = self.state_mod.WeaverSettings(group="g")

        worker = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            slug="worker",
            group="g",
            cell_type="agent",
            status="idle",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_repo_root="/repo",
            worktree_branch="loom/worker",
            git_root="/repo",
        )
        state.agents[worker.id] = worker
        state.groups["g"].append(worker.id)

        product = self.state_mod.BoardTask(
            id="LOOM:1",
            task="Add Events tab",
            group="g",
            lane="Done",
            action_name="feature/implement",
            agent_id=worker.id,
            created_at="2026-04-07T10:00:00+00:00",
            updated_at="2026-04-07T10:30:00+00:00",
            lane_entered_at="2026-04-07T10:00:00+00:00",
        )
        review = self.state_mod.BoardTask(
            id="LOOM:1:1",
            task="Review Events implementation",
            group="g",
            lane="Done",
            action_name="feature/review",
            parent_task_id=product.id,
            pipeline_root_id=product.id,
            pipeline_depth=1,
            agent_id=worker.id,
            created_at="2026-04-07T11:00:00+00:00",
            updated_at="2026-04-07T11:30:00+00:00",
            lane_entered_at="2026-04-07T11:00:00+00:00",
            worktree_boundary={
                "version": "1",
                "repo_root": "/repo",
                "branch": "loom/worker",
                "status": "open",
                "recorded_at": "2026-04-07T11:30:00+00:00",
                "commit_sha": "abc123",
                "recorded_by_agent_id": worker.id,
            },
        )
        state.board_tasks[product.id] = product
        state.board_tasks[review.id] = review
        return state, product

    def test_to_dict_includes_weaver_streams_snapshot(self):
        state, _product = self._make_state_with_open_stream()

        payload = state.to_dict()

        self.assertIn("weaver_streams", payload)
        self.assertIn("g", payload["weaver_streams"])
        summary = payload["weaver_streams"]["g"]
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["items"][0]["branch"], "loom/worker")
        self.assertEqual(summary["items"][0]["state"], "ready_to_merge")

    async def test_broadcast_appends_weaver_stream_deltas_for_task_changes(self):
        state, product = self._make_state_with_open_stream()

        class FakeWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(json.loads(msg))

        ws = FakeWS()
        state._ws_clients.add(ws)
        state._emit("task_upsert", **self.state_mod.asdict(product))

        # Primary broadcast: UI-facing ops land immediately; the expensive
        # weaver-stream recompute is deferred to a background worker so
        # mutations don't wait on `git for-each-ref`.
        await state.broadcast()

        self.assertEqual(len(ws.messages), 1, ws.messages)
        primary_ops = ws.messages[0]["ops"]
        self.assertEqual(
            [op["op"] for op in primary_ops if op["op"] == "task_upsert"],
            ["task_upsert"],
        )
        self.assertFalse(
            any(op["op"] == "weaver_streams" for op in primary_ops),
            "weaver_streams should not block the primary delta frame",
        )

        # Drain the deferred recompute and its follow-up broadcast.
        self.assertIsNotNone(state._weaver_recompute_task)
        await state._weaver_recompute_task

        self.assertEqual(len(ws.messages), 2, ws.messages)
        followup_ops = ws.messages[1]["ops"]
        stream_ops = [
            op for op in followup_ops if op["op"] == "weaver_streams"
        ]
        self.assertEqual(len(stream_ops), 1)
        self.assertEqual(stream_ops[0]["group"], "g")
        self.assertEqual(stream_ops[0]["streams"]["count"], 1)
        self.assertEqual(
            stream_ops[0]["streams"]["items"][0]["branch"],
            "loom/worker",
        )


class AgentCellActivityClockTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def test_heartbeat_updates_only_heartbeat_and_activity_alias(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_progress_at=100.0,
            last_heartbeat_at=100.0,
        )

        changed = cell.mark_heartbeat(200.0)

        self.assertTrue(changed)
        self.assertEqual(cell.last_progress_at, 100.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)
        self.assertEqual(cell.last_event_at, 200.0)

    def test_progress_updates_progress_heartbeat_and_activity_alias(self):
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            last_progress_at=100.0,
            last_heartbeat_at=150.0,
        )

        changed = cell.mark_progress(200.0)

        self.assertTrue(changed)
        self.assertEqual(cell.last_progress_at, 200.0)
        self.assertEqual(cell.last_heartbeat_at, 200.0)
        self.assertEqual(cell.last_activity_at, 200.0)
        self.assertEqual(cell.last_event_at, 200.0)
