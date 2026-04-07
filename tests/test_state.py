import importlib
import sys
import types
import unittest


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


class MatrixStateBoardWorkflowTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        return state

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

        state.board_move_task(task.id, "Done")

        finished = state.board_tasks[task.id]
        self.assertEqual(finished.lane, "Done")
        self.assertEqual(finished.labels, ["keep"])

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
