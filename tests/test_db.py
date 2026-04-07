import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from loom.db import LoomDB

install_aiohttp_stub()
from loom.state import AgentCell, BoardTask, GroupSettings, Schedule


class LoomDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = LoomDB(Path(self.tmp.name) / "loom.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def test_load_all_roundtrips_json_and_boolean_fields(self):
        cell = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            session_id="session-1",
            command="codex",
            directory="/repo",
            worktree_path="/repo/.loom/worktrees/agent-1",
            worktree_branch="loom/worker",
            worktree_repo_root="/repo",
            worktree_base_branch="main",
            worktree_auto_checkpoint=True,
            checkpoint_on_progress=True,
            worktree_merge_squash=False,
            agent_type="codex",
            agent_session_id="agent-session",
            session_resume=False,
            idle_timeout=9,
            tasks_dispatched=3,
        )
        self.db.save_agent(cell)
        self.db.save_groups({"g": [cell.id]}, {"g": "g"})
        self.db.save_group_members("g", [cell.id])
        self.db.save_group_settings(
            "g",
            GroupSettings(
                notifications=True,
                board_default_labels=["ready"],
                worktree_symlinks=["shared/config.yml"],
                agent_session_resume=False,
                worktree_merge_squash=False,
            ),
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Ship feature",
                description="Exercise persistence",
                slug="ship-feature",
                group="g",
                action_name="feature/implement",
                action_vars={"TEST_COMMAND": "python3 -m unittest"},
                agent_template="researcher",
                lane="In Progress",
                position=2,
                agent_id=cell.id,
                labels=["loom:blocked", "keep"],
                created_at="2026-04-06T00:00:00+00:00",
                updated_at="2026-04-06T01:00:00+00:00",
                parent_task_id="parent-1",
                pipeline_depth=2,
                pipeline_root_id="root-1",
                status="Reviewing",
                scheduled_at="2026-04-07T10:00:00+00:00",
                messages=[{"action": "progress", "message": "Working"}],
                depends_on=["dep-1"],
                attachments=[{"path": "/tmp/mock.png", "filename": "mock.png"}],
            )
        )
        self.db.save_schedule(
            Schedule(
                id="sched-1",
                name="Morning sync",
                slug="morning-sync",
                task_template="Standup {date}",
                description="Recurring schedule",
                group="g",
                action_name="feature/review",
                action_vars={"CHECK": "docs"},
                labels=["ops"],
                cron_expr="0 8 * * 1-5",
                timezone="America/New_York",
                enabled=False,
                next_run_at="2026-04-07T12:00:00+00:00",
            )
        )
        self.db.save_board_lanes(["Backlog", "To Do", "In Progress", "Done"])

        loaded = self.db.load_all()

        self.assertEqual(loaded["groups"], {"g": ["agent-1"]})
        self.assertEqual(loaded["group_slugs"], {"g": "g"})
        self.assertFalse(loaded["agents"]["agent-1"]["session_resume"])
        self.assertTrue(loaded["agents"]["agent-1"]["worktree_auto_checkpoint"])
        self.assertEqual(
            loaded["group_settings"]["g"]["board_default_labels"],
            ["ready"],
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["worktree_symlinks"],
            ["shared/config.yml"],
        )
        self.assertFalse(loaded["group_settings"]["g"]["worktree_merge_squash"])
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["action_vars"],
            {"TEST_COMMAND": "python3 -m unittest"},
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["depends_on"],
            ["dep-1"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["attachments"][0]["filename"],
            "mock.png",
        )
        self.assertFalse(loaded["schedules"]["sched-1"]["enabled"])
        self.assertEqual(loaded["schedules"]["sched-1"]["labels"], ["ops"])

    def test_panel_event_paging_and_trim_keep_recent_events(self):
        for i in range(1, 6):
            self.db.save_panel_event(
                {
                    "id": i,
                    "timestamp": float(i),
                    "kind": "task_completed",
                    "cell_id": f"agent-{i}",
                    "agent_name": f"Agent {i}",
                    "group": "g",
                    "message": f"done {i}",
                    "task_id": f"task-{i}",
                }
            )

        self.assertEqual(self.db.get_panel_event_max_id(), 5)
        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=3)],
            [3, 4, 5],
        )
        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=2, before_id=4)],
            [2, 3],
        )

        self.db.trim_panel_events(2)

        self.assertEqual(
            [evt["id"] for evt in self.db.load_panel_events(limit=10)],
            [4, 5],
        )

    def test_weaver_settings_and_journal_roundtrip(self):
        self.db.save_weaver_settings(
            "g",
            {
                "group": "g",
                "push_interval": 30,
                "max_interval": 120,
                "paused": True,
                "custom_instructions": "Focus on regressions.",
                "pending_question": "Need approval",
                "enabled_events": ["task_completed"],
                "weaver_provider": "codex",
                "weaver_boot_command": "codex --model gpt-5",
            },
        )
        first = self.db.save_journal_entry("g", 10.0, "decision", "Ship it")
        second = self.db.save_journal_entry("g", 20.0, "plan", "Add tests")

        loaded = self.db.load_weaver_settings("g")
        self.assertEqual(loaded["pending_question"], "Need approval")
        self.assertEqual(loaded["enabled_events"], ["task_completed"])
        self.assertEqual(loaded["weaver_boot_command"], "codex --model gpt-5")

        entries = self.db.load_journal_entries("g", limit=10)

        self.assertEqual([entry["id"] for entry in entries], [second, first])
        self.assertEqual(entries[0]["type"], "plan")
        self.assertEqual(
            self.db.load_journal_entries("g", limit=10, entry_type="decision"),
            [entries[1]],
        )
