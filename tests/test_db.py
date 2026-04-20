import tempfile
import unittest
import logging
from pathlib import Path
import json
import sqlite3
from unittest import mock

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

    def _seed_stage1a_db(
        self,
        filename: str,
        *,
        agents=None,
        tasks=None,
        group_settings=None,
    ):
        path = Path(self.tmp.name) / filename
        seeded = LoomDB(path)
        seeded.init()
        if agents:
            for agent in agents:
                seeded.save_agent(agent)
        if tasks:
            for task in tasks:
                seeded.save_board_task(task)
        if group_settings:
            for group_name, settings in group_settings.items():
                seeded.save_group_settings(group_name, settings)
        seeded.close()

        conn = sqlite3.connect(str(path))
        conn.execute(
            "ALTER TABLE agents ADD COLUMN template TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE agents ADD COLUMN created_by_weaver_id TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN weaver_owner_id TEXT NOT NULL DEFAULT ''"
        )
        for agent in agents or []:
            item = dict(agent) if isinstance(agent, dict) else agent.__dict__
            conn.execute(
                "UPDATE agents SET template=?, created_by_weaver_id=? WHERE id=?",
                (
                    str(item.get("template", "") or item.get("role", "") or ""),
                    str(
                        item.get("created_by_weaver_id", "")
                        or item.get("owner_engineer_id", "")
                        or ""
                    ),
                    str(item.get("id", "") or ""),
                ),
            )
        for task in tasks or []:
            item = dict(task) if isinstance(task, dict) else task.__dict__
            conn.execute(
                "UPDATE board_tasks SET weaver_owner_id=? WHERE id=?",
                (
                    str(
                        item.get("weaver_owner_id", "")
                        or item.get("assigned_engineer_id", "")
                        or ""
                    ),
                    str(item.get("id", "") or ""),
                ),
            )
        conn.execute(
            "UPDATE meta SET value='1' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "UPDATE meta SET value='2026-04-19T00:00:00+00:00' "
            "WHERE key='schema_kinds_migration_migrated_at'"
        )
        conn.execute(
            "UPDATE agents SET kind='', role='', owner_engineer_id='', "
            "hired_by_architect_id='', persistent=0"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='', "
            "created_by_architect_id='', suggested_action=''"
        )
        conn.commit()
        conn.close()
        return path

    def _add_legacy_board_task_owner_column(self, conn=None):
        close_conn = False
        if conn is None:
            conn = sqlite3.connect(str(self.db.db_path))
            close_conn = True
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN weaver_owner_id TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()
        if close_conn:
            conn.close()

    def test_load_all_roundtrips_json_and_boolean_fields(self):
        cell = AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            slug="worker",
            cell_type="agent",
            terminal_backend="pty",
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
            created_by_weaver_id="weaver-1",
            kind="worker",
            role="researcher",
            owner_engineer_id="weaver-1",
            hired_by_architect_id="architect-1",
            persistent=True,
        )
        self.db.save_agent(cell)
        self.db.save_groups({"g": [cell.id]}, {"g": "g"})
        self.db.save_group_members("g", [cell.id])
        self.db.save_group_settings(
            "g",
            GroupSettings(
                default_terminal_backend="pty",
                notifications=True,
                agent_model="gpt-5",
                agent_reasoning_effort="high",
                board_default_labels=["ready"],
                worktree_symlinks=["shared/config.yml"],
                agent_session_resume=False,
                worktree_merge_squash=False,
                worktree_merge_cleanup="close_remove",
                worktree_merge_preserve_diff=True,
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
                assigned_engineer_id="engineer-1",
                created_by_architect_id="architect-1",
                suggested_action="feature/review",
                reply_agent_id="agent-2",
                labels=["loom:blocked", "keep"],
                created_at="2026-04-06T00:00:00+00:00",
                updated_at="2026-04-06T01:00:00+00:00",
                lane_entered_at="2026-04-06T00:30:00+00:00",
                parent_task_id="parent-1",
                pipeline_depth=2,
                pipeline_root_id="root-1",
                status="Reviewing",
                scheduled_at="2026-04-07T10:00:00+00:00",
                messages=[{"action": "progress", "message": "Working"}],
                depends_on=["dep-1"],
                attachments=[{"path": "/tmp/mock.png", "filename": "mock.png"}],
                health_state="idle-risk",
                health_since="2026-04-06T01:05:00+00:00",
                health_details={"reasons": ["progress_silence_warning"]},
                verification_mode="deploy",
                verification_state="pending",
                verification_notes="Need manual smoke after deploy",
                verification_updated_at="2026-04-06T01:10:00+00:00",
                verification_updated_by="worker",
                verification_summary={
                    "tests_run": "python3 -m unittest",
                    "manual_smoke_done": False,
                    "deploy_needed": True,
                    "human_validation_pending": "Confirm dashboard loads",
                },
                worktree_boundary={
                    "version": "1",
                    "branch": "loom/worker",
                    "repo_root": "/repo",
                    "base_branch": "main",
                    "commit_sha": "abc123",
                    "kind": "checkpoint",
                    "status": "open",
                },
                resume_after_boundary_task_id="task-0",
                artifacts=[{
                    "id": "artifact-1",
                    "type": "test_report",
                    "title": "pytest",
                    "path": "/tmp/pytest.txt",
                    "summary": "2 failed, 18 passed",
                    "prompt": {"mode": "summary"},
                    "provenance": {"source": "agent", "agent_id": "agent-1"},
                    "storage": {"kind": "path", "path": "/tmp/pytest.txt"},
                    "lifecycle": {"owner": "task", "cleanup": "delete_with_task"},
                }],
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
        self.db.save_auto_dispatch_queue("g", [
            {
                "task_id": "task-1",
                "agent_group": "release",
                "max_concurrent": 2,
                "target_agent_id": "agent-1",
                "weaver_owner_id": "weaver-1",
                "enqueued_at": "2026-04-07T09:00:00+00:00",
            }
        ])
        self.db.save_board_lanes(["Backlog", "To Do", "In Progress", "Done"])

        loaded = self.db.load_all()

        self.assertEqual(loaded["groups"], {"g": ["agent-1"]})
        self.assertEqual(loaded["group_slugs"], {"g": "g"})
        self.assertEqual(loaded["agents"]["agent-1"]["terminal_backend"], "pty")
        self.assertFalse(loaded["agents"]["agent-1"]["session_resume"])
        self.assertTrue(loaded["agents"]["agent-1"]["worktree_auto_checkpoint"])
        self.assertEqual(
            loaded["agents"]["agent-1"]["created_by_weaver_id"],
            "weaver-1",
        )
        self.assertEqual(loaded["agents"]["agent-1"]["kind"], "worker")
        self.assertEqual(loaded["agents"]["agent-1"]["role"], "researcher")
        self.assertEqual(
            loaded["agents"]["agent-1"]["owner_engineer_id"],
            "weaver-1",
        )
        self.assertEqual(
            loaded["agents"]["agent-1"]["hired_by_architect_id"],
            "architect-1",
        )
        self.assertTrue(loaded["agents"]["agent-1"]["persistent"])
        self.assertEqual(
            loaded["group_settings"]["g"]["default_terminal_backend"],
            "pty",
        )
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
            loaded["group_settings"]["g"]["agent_model"],
            "gpt-5",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["agent_reasoning_effort"],
            "high",
        )
        self.assertEqual(
            loaded["group_settings"]["g"]["worktree_merge_cleanup"],
            "close_remove",
        )
        self.assertTrue(
            loaded["group_settings"]["g"]["worktree_merge_preserve_diff"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["action_vars"],
            {"TEST_COMMAND": "python3 -m unittest"},
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["depends_on"],
            ["dep-1"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["reply_agent_id"],
            "agent-2",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["assigned_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["created_by_architect_id"],
            "architect-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["suggested_action"],
            "feature/review",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["lane_entered_at"],
            "2026-04-06T00:30:00+00:00",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["attachments"][0]["filename"],
            "mock.png",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["health_state"],
            "idle-risk",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["health_details"]["reasons"],
            ["progress_silence_warning"],
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["artifacts"][0]["type"],
            "test_report",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_mode"],
            "deploy",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_state"],
            "pending",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["verification_summary"]["tests_run"],
            "python3 -m unittest",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["worktree_boundary"]["status"],
            "open",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["resume_after_boundary_task_id"],
            "task-0",
        )
        self.assertFalse(loaded["schedules"]["sched-1"]["enabled"])
        self.assertEqual(loaded["schedules"]["sched-1"]["labels"], ["ops"])
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["task_id"],
            "task-1",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["agent_group"],
            "release",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["target_agent_id"],
            "agent-1",
        )
        self.assertEqual(
            loaded["auto_dispatch_queues"]["g"][0]["weaver_owner_id"],
            "weaver-1",
        )

    def test_save_agent_and_board_task_update_preserve_kinds_fields(self):
        cell = AgentCell(
            id="agent-kinds",
            name="Kinds Worker",
            group="g",
            slug="kinds-worker",
            kind="worker",
            role="researcher",
            owner_engineer_id="engineer-1",
            hired_by_architect_id="architect-1",
            persistent=False,
        )
        task = BoardTask(
            id="task-kinds",
            task="Kinds task",
            group="g",
            slug="kinds-task",
            agent_id=cell.id,
            assigned_engineer_id="engineer-1",
            created_by_architect_id="architect-1",
            suggested_action="feature/review",
        )

        self.db.save_agent(cell)
        self.db.save_board_task(task)

        cell.kind = "architect"
        cell.role = "lead"
        cell.owner_engineer_id = "engineer-2"
        cell.hired_by_architect_id = "architect-2"
        cell.persistent = True
        task.assigned_engineer_id = "engineer-2"
        task.created_by_architect_id = "architect-2"
        task.suggested_action = "feature/fix-review"

        self.db.save_agent(cell)
        self.db.save_board_task(task)

        row = self.db._conn.execute(
            "SELECT kind, role, owner_engineer_id, hired_by_architect_id, "
            "persistent FROM agents WHERE id=?",
            (cell.id,),
        ).fetchone()
        self.assertEqual(
            row,
            ("architect", "lead", "engineer-2", "architect-2", 1),
        )

        task_row = self.db._conn.execute(
            "SELECT assigned_engineer_id, created_by_architect_id, "
            "suggested_action FROM board_tasks WHERE id=?",
            (task.id,),
        ).fetchone()
        self.assertEqual(
            task_row,
            ("engineer-2", "architect-2", "feature/fix-review"),
        )

        loaded = self.db.load_all()
        self.assertEqual(loaded["agents"][cell.id]["kind"], "architect")
        self.assertEqual(loaded["agents"][cell.id]["role"], "lead")
        self.assertEqual(
            loaded["agents"][cell.id]["owner_engineer_id"],
            "engineer-2",
        )
        self.assertEqual(
            loaded["agents"][cell.id]["hired_by_architect_id"],
            "architect-2",
        )
        self.assertTrue(loaded["agents"][cell.id]["persistent"])
        self.assertEqual(
            loaded["board_tasks"][task.id]["assigned_engineer_id"],
            "engineer-2",
        )
        self.assertEqual(
            loaded["board_tasks"][task.id]["created_by_architect_id"],
            "architect-2",
        )
        self.assertEqual(
            loaded["board_tasks"][task.id]["suggested_action"],
            "feature/fix-review",
        )

    def test_save_agent_maps_legacy_inputs_into_kinds_columns(self):
        self.db.save_agent(
            AgentCell(
                id="agent-dual-write",
                name="Dual Writer",
                group="g",
                template="researcher",
                created_by_weaver_id="weaver-1",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-write'"
            ).fetchone(),
            ("researcher", "weaver-1"),
        )

        self.db.save_agent(
            AgentCell(
                id="agent-dual-write",
                name="Dual Writer",
                group="g",
                role="planner",
                owner_engineer_id="weaver-2",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-write'"
            ).fetchone(),
            ("planner", "weaver-2"),
        )

    def test_save_agent_prefers_kinds_columns_over_legacy_inputs(self):
        self.db.save_agent(
            AgentCell(
                id="agent-dual-conflict",
                name="Conflict",
                group="g",
                template="researcher",
                role="planner",
                created_by_weaver_id="weaver-1",
                owner_engineer_id="weaver-2",
            )
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT role, owner_engineer_id "
                "FROM agents WHERE id='agent-dual-conflict'"
            ).fetchone(),
            ("planner", "weaver-2"),
        )

    def test_save_board_task_maps_legacy_owner_into_new_column(self):
        self.db.save_board_task(
            {
                "id": "task-dual-assigned",
                "task": "Assigned-first",
                "group": "g",
                "assigned_engineer_id": "weaver-1",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-assigned'"
            ).fetchone(),
            ("weaver-1",),
        )

        self.db.save_board_task(
            {
                "id": "task-dual-legacy",
                "task": "Legacy-first",
                "group": "g",
                "weaver_owner_id": "weaver-2",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-legacy'"
            ).fetchone(),
            ("weaver-2",),
        )

    def test_save_board_task_prefers_assigned_engineer_over_legacy_owner(self):
        self.db.save_board_task(
            {
                "id": "task-dual-conflict",
                "task": "Conflict",
                "group": "g",
                "assigned_engineer_id": "engineer-new",
                "weaver_owner_id": "engineer-legacy",
            }
        )
        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id "
                "FROM board_tasks WHERE id='task-dual-conflict'"
            ).fetchone(),
            ("engineer-new",),
        )

    def test_save_board_task_uses_legacy_owner_when_new_column_is_empty(self):
        self.db.save_board_task(
            {
                "id": "task-no-legacy-owner",
                "task": "No legacy owner column",
                "group": "g",
                "weaver_owner_id": "engineer-legacy",
            }
        )

        self.assertEqual(
            self.db._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks "
                "WHERE id='task-no-legacy-owner'"
            ).fetchone(),
            ("engineer-legacy",),
        )

    def test_load_all_restores_board_filters_by_group(self):
        filters = {
            "alpha": {
                "search_query": "deploy",
                "filter_labels": ["bug"],
                "filter_actions": ["triage"],
                "filter_agents": ["agent-1"],
                "pre_filter_lane": "Backlog",
            }
        }
        self.db.save_ui_state("board_filters_by_group", json.dumps(filters))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_filters_by_group"], filters)

    def test_save_all_preserves_kinds_fields(self):
        self.db.save_all(
            {
                "agents": {
                    "agent-1": {
                        "id": "agent-1",
                        "name": "Architect",
                        "group": "g",
                        "slug": "architect",
                        "kind": "architect",
                        "role": "lead",
                        "owner_engineer_id": "engineer-1",
                        "hired_by_architect_id": "architect-root",
                        "persistent": 1,
                    }
                },
                "groups": {"g": ["agent-1"]},
                "group_slugs": {"g": "g"},
                "board_tasks": {
                    "task-1": {
                        "id": "task-1",
                        "task": "Plan next wave",
                        "group": "g",
                        "slug": "plan-next-wave",
                        "assigned_engineer_id": "engineer-1",
                        "created_by_architect_id": "architect-root",
                        "suggested_action": "feature/review",
                    }
                },
            }
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["agents"]["agent-1"]["kind"], "architect")
        self.assertEqual(loaded["agents"]["agent-1"]["role"], "lead")
        self.assertEqual(
            loaded["agents"]["agent-1"]["owner_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["agents"]["agent-1"]["hired_by_architect_id"],
            "architect-root",
        )
        self.assertTrue(loaded["agents"]["agent-1"]["persistent"])
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["assigned_engineer_id"],
            "engineer-1",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["created_by_architect_id"],
            "architect-root",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-1"]["suggested_action"],
            "feature/review",
        )

    def test_save_all_coalesces_legacy_and_kinds_fields(self):
        self.db.save_all(
            {
                "agents": {
                    "agent-1": {
                        "id": "agent-1",
                        "name": "Worker",
                        "group": "g",
                        "role": "researcher",
                        "owner_engineer_id": "weaver-1",
                    },
                    "agent-2": {
                        "id": "agent-2",
                        "name": "Legacy Worker",
                        "group": "g",
                        "template": "planner",
                        "created_by_weaver_id": "weaver-2",
                    },
                },
                "groups": {"g": ["agent-1", "agent-2"]},
                "group_slugs": {"g": "g"},
                "board_tasks": {
                    "task-1": {
                        "id": "task-1",
                        "task": "Assigned-first",
                        "group": "g",
                        "assigned_engineer_id": "weaver-1",
                    },
                    "task-2": {
                        "id": "task-2",
                        "task": "Legacy-first",
                        "group": "g",
                        "weaver_owner_id": "weaver-2",
                    },
                },
            }
        )

        agent_rows = self.db._conn.execute(
            "SELECT id, role, owner_engineer_id "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            agent_rows,
            [
                ("agent-1", "researcher", "weaver-1"),
                ("agent-2", "planner", "weaver-2"),
            ],
        )
        task_rows = self.db._conn.execute(
            "SELECT id, assigned_engineer_id "
            "FROM board_tasks ORDER BY id"
        ).fetchall()
        self.assertEqual(
            task_rows,
            [
                ("task-1", "weaver-1"),
                ("task-2", "weaver-2"),
            ],
        )

    def test_init_migrates_legacy_task_ids_and_rewrites_references(self):
        legacy_db = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy_db)
        conn.executescript(
            """
            CREATE TABLE groups (name TEXT PRIMARY KEY, slug TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE group_members (group_name TEXT NOT NULL, agent_id TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (group_name, agent_id));
            CREATE TABLE board_tasks (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                slug TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                action_name TEXT NOT NULL DEFAULT '',
                action_vars TEXT NOT NULL DEFAULT '{}',
                agent_template TEXT NOT NULL DEFAULT '',
                instructions TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                criteria TEXT NOT NULL DEFAULT '',
                lane TEXT NOT NULL DEFAULT 'Backlog',
                position INTEGER NOT NULL DEFAULT 0,
                agent_id TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                lane_entered_at TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                external_url TEXT NOT NULL DEFAULT '',
                parent_task_id TEXT NOT NULL DEFAULT '',
                pipeline_depth INTEGER NOT NULL DEFAULT 0,
                pipeline_root_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]',
                depends_on TEXT NOT NULL DEFAULT '[]',
                attachments TEXT NOT NULL DEFAULT '[]',
                health_state TEXT NOT NULL DEFAULT 'healthy',
                health_since TEXT NOT NULL DEFAULT '',
                health_details TEXT NOT NULL DEFAULT '{}',
                artifacts TEXT NOT NULL DEFAULT '[]',
                verification_mode TEXT NOT NULL DEFAULT '',
                verification_state TEXT NOT NULL DEFAULT '',
                verification_notes TEXT NOT NULL DEFAULT '',
                verification_updated_at TEXT NOT NULL DEFAULT '',
                verification_updated_by TEXT NOT NULL DEFAULT '',
                verification_summary TEXT NOT NULL DEFAULT '{}',
                worktree_boundary TEXT NOT NULL DEFAULT '{}',
                resume_after_boundary_task_id TEXT NOT NULL DEFAULT '',
                archived_at TEXT NOT NULL DEFAULT '',
                archived_from_lane TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE auto_dispatch_queue (
                group_name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL,
                agent_group TEXT NOT NULL DEFAULT '',
                max_concurrent INTEGER NOT NULL DEFAULT 1,
                target_agent_id TEXT NOT NULL DEFAULT '',
                enqueued_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (group_name, position)
            );
            CREATE TABLE panel_events (
                id INTEGER PRIMARY KEY,
                timestamp REAL NOT NULL,
                kind TEXT NOT NULL,
                cell_id TEXT NOT NULL DEFAULT '',
                agent_name TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL DEFAULT '',
                task_template TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                action_name TEXT NOT NULL DEFAULT '',
                action_vars TEXT NOT NULL DEFAULT '{}',
                agent_template TEXT NOT NULL DEFAULT '',
                labels TEXT NOT NULL DEFAULT '[]',
                cron_expr TEXT NOT NULL DEFAULT '',
                scheduled_at TEXT NOT NULL DEFAULT '',
                timezone TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at TEXT NOT NULL DEFAULT '',
                next_run_at TEXT NOT NULL DEFAULT '',
                run_count INTEGER NOT NULL DEFAULT 0,
                last_task_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE memory_entries (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL DEFAULT '',
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                retention_kind TEXT NOT NULL DEFAULT 'durable',
                expires_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(entry_id, target_kind, target_ref)
            );
            CREATE TABLE agent_history (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT DEFAULT '',
                "group" TEXT DEFAULT '',
                agent_type TEXT DEFAULT '',
                template TEXT DEFAULT '',
                created_at REAL NOT NULL,
                removed_at REAL,
                worktree_branch TEXT DEFAULT '',
                total_tokens_in INTEGER DEFAULT 0,
                total_tokens_out INTEGER DEFAULT 0,
                total_tasks INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE agent_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_title TEXT NOT NULL,
                started_at REAL NOT NULL,
                completed_at REAL,
                outcome TEXT DEFAULT ''
            );
            CREATE TABLE agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                message TEXT DEFAULT ''
            );
            """
        )
        conn.execute("INSERT INTO groups (name, slug, position) VALUES ('Loom', 'loom', 0)")
        conn.execute(
            "INSERT INTO board_tasks (id, task, group_name, created_at, updated_at, lane_entered_at) "
            "VALUES ('task-root', 'Root', 'Loom', '2026-04-08T10:00:00+00:00', '2026-04-08T10:00:00+00:00', '2026-04-08T10:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO board_tasks (id, task, group_name, parent_task_id, pipeline_depth, pipeline_root_id, depends_on, created_at, updated_at, lane_entered_at) "
            "VALUES ('task-child', 'Child', 'Loom', 'task-root', 1, 'task-root', '[\"task-root\"]', '2026-04-08T10:10:00+00:00', '2026-04-08T10:10:00+00:00', '2026-04-08T10:10:00+00:00')"
        )
        conn.execute(
            "INSERT INTO auto_dispatch_queue (group_name, position, task_id) VALUES ('Loom', 0, 'task-child')"
        )
        conn.execute(
            "INSERT INTO panel_events (id, timestamp, kind, task_id) VALUES (1, 1.0, 'task_dispatched', 'task-child')"
        )
        conn.execute(
            "INSERT INTO schedules (id, name, group_name, last_task_id, created_at, updated_at) VALUES ('sched', 'Nightly', 'Loom', 'task-root', '', '')"
        )
        conn.execute(
            "INSERT INTO memory_entries (id, scope_kind, scope_ref, entry_type, content, task_id, created_at, updated_at) "
            "VALUES ('mem-1', 'task', 'task-child', 'note', 'hello', 'task-child', 1, 1)"
        )
        conn.execute(
            "INSERT INTO memory_links (entry_id, target_kind, target_ref, created_at) VALUES ('mem-1', 'task', 'task-root', 1)"
        )
        conn.execute(
            "INSERT INTO agent_history (id, name, created_at) VALUES ('agent-1', 'Worker', 1)"
        )
        conn.execute(
            "INSERT INTO agent_tasks (agent_id, task_id, task_title, started_at) VALUES ('agent-1', 'task-root', 'Root', 1)"
        )
        conn.execute(
            "INSERT INTO agent_messages (agent_id, task_id, timestamp, action, message) VALUES ('agent-1', 'task-child', 1, 'progress', 'working')"
        )
        conn.commit()
        conn.close()

        migrated_db = LoomDB(legacy_db)
        migrated_db.init()
        self.addCleanup(migrated_db.close)
        loaded = migrated_db.load_all()

        self.assertIn("LOOM:1", loaded["board_tasks"])
        self.assertIn("LOOM:1:1", loaded["board_tasks"])
        self.assertEqual(loaded["board_tasks"]["LOOM:1:1"]["parent_task_id"], "LOOM:1")
        self.assertEqual(loaded["board_tasks"]["LOOM:1:1"]["pipeline_root_id"], "LOOM:1")
        self.assertEqual(loaded["board_tasks"]["LOOM:1:1"]["depends_on"], ["LOOM:1"])
        self.assertEqual(loaded["board_tasks"]["LOOM:1"]["reply_agent_id"], "")
        self.assertEqual(loaded["auto_dispatch_queues"]["Loom"][0]["task_id"], "LOOM:1:1")
        self.assertEqual(
            loaded["auto_dispatch_queues"]["Loom"][0]["weaver_owner_id"],
            "",
        )
        self.assertEqual(loaded["schedules"]["sched"]["last_task_id"], "LOOM:1")
        self.assertEqual(loaded["task_id_aliases"]["task-root"], "LOOM:1")
        self.assertEqual(loaded["task_id_aliases"]["task-child"], "LOOM:1:1")

    def test_load_all_restores_board_saved_views_by_group(self):
        views = {
            "alpha": [
                {
                    "name": "Review Queue",
                    "search_query": "review",
                    "filter_labels": ["loom:blocked"],
                    "filter_actions": ["feature/review"],
                    "filter_agents": [],
                }
            ]
        }
        self.db.save_ui_state("board_saved_views_by_group", json.dumps(views))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_saved_views_by_group"], views)

    def test_load_all_restores_board_lane_sorts_by_group(self):
        sorts = {
            "alpha": {
                "Backlog": "due",
                "Done": "oldest",
            }
        }
        self.db.save_ui_state("board_lane_sorts_by_group", json.dumps(sorts))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_lane_sorts_by_group"], sorts)

    def test_load_all_restores_board_card_density_by_group(self):
        density = {
            "alpha": "compact",
            "beta": "detailed",
        }
        self.db.save_ui_state("board_card_density_by_group",
                              json.dumps(density))

        loaded = self.db.load_all()

        self.assertEqual(loaded["board_card_density_by_group"], density)

    def test_load_all_restores_events_dismissed_attention(self):
        dismissed = {
            "ask-1": 123.0,
            "agent-1": 456.0,
        }
        self.db.save_ui_state(
            "events_dismissed_attention",
            json.dumps(dismissed),
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["events_dismissed_attention"], dismissed)

    def test_load_all_restores_standalone_panel_layout(self):
        layout = {
            "version": 1,
            "bottom": {
                "open": True,
                "size": 280,
                "tabs": ["board", "weaver"],
                "active": "board",
            },
            "right": {
                "open": True,
                "size": 320,
                "tabs": ["context", "events"],
                "active": "context",
            },
            "floats": {
                "actions": {
                    "x": 40,
                    "y": 50,
                    "width": 420,
                    "height": 320,
                    "z": 3,
                },
            },
        }
        self.db.save_ui_state(
            "standalone_panel_layout",
            json.dumps(layout),
        )

        loaded = self.db.load_all()

        self.assertEqual(loaded["standalone_panel_layout"], layout)

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
                "heartbeat_interval": 180,
                "default_worker_concurrency": 4,
                "autonomy_mode": "aggressive_auto_continue",
                "wave_size_preference": "large",
                "same_agent_follow_up_preference": "prefer_same_agent",
                "digest_verbosity": "detailed",
                "escalation_style": "keep_moving",
                "paused": True,
                "custom_instructions": "Focus on regressions.",
                "restrict_to_created_agents": True,
                "pending_question": "Need approval",
                "pending_note": "FYI: release notes are ready",
                "pending_note_kind": "note",
                "enabled_events": ["task_completed"],
                "weaver_provider": "codex",
                "weaver_boot_command": "codex --model gpt-5",
                "weaver_model": "gpt-5.1",
                "weaver_reasoning_effort": "high",
                "weaver_directory": "/repo/.loom/weaver",
                "weaver_profile": "Ops",
                "weaver_shell": "fish",
                "weaver_tab_color": "none",
            },
        )
        first = self.db.save_journal_entry("g", 10.0, "decision", "Ship it")
        second = self.db.save_journal_entry("g", 20.0, "plan", "Add tests")

        loaded = self.db.load_weaver_settings("g")
        self.assertEqual(loaded["pending_question"], "Need approval")
        self.assertTrue(loaded["restrict_to_created_agents"])
        self.assertEqual(loaded["pending_note"], "FYI: release notes are ready")
        self.assertEqual(loaded["pending_note_kind"], "note")
        self.assertEqual(loaded["enabled_events"], ["task_completed"])
        self.assertEqual(loaded["heartbeat_interval"], 180)
        self.assertEqual(loaded["default_worker_concurrency"], 4)
        self.assertEqual(loaded["autonomy_mode"], "aggressive_auto_continue")
        self.assertEqual(loaded["wave_size_preference"], "large")
        self.assertEqual(
            loaded["same_agent_follow_up_preference"], "prefer_same_agent"
        )
        self.assertEqual(loaded["digest_verbosity"], "detailed")
        self.assertEqual(loaded["escalation_style"], "keep_moving")
        self.assertEqual(loaded["weaver_boot_command"], "codex --model gpt-5")
        self.assertEqual(loaded["weaver_model"], "gpt-5.1")
        self.assertEqual(loaded["weaver_reasoning_effort"], "high")
        self.assertEqual(loaded["weaver_directory"], "/repo/.loom/weaver")
        self.assertEqual(loaded["weaver_profile"], "Ops")
        self.assertEqual(loaded["weaver_shell"], "fish")
        self.assertEqual(loaded["weaver_tab_color"], "none")

        entries = self.db.load_journal_entries("g", limit=10)

        self.assertEqual([entry["id"] for entry in entries], [second, first])
        self.assertEqual(entries[0]["type"], "plan")
        self.assertEqual(
            self.db.load_journal_entries("g", limit=10, entry_type="decision"),
            [entries[1]],
        )

    def test_weaver_settings_load_backfills_heartbeat_from_legacy_rows(self):
        self.db._conn.execute(
            """
            INSERT INTO weaver_settings
                (group_name, push_interval, max_interval, paused,
                 custom_instructions, pending_question, enabled_events,
                 weaver_provider, weaver_boot_command)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                60,
                240,
                0,
                "",
                "",
                '["task_completed"]',
                "",
                "",
            ),
        )
        self.db._conn.commit()

        loaded = self.db.load_weaver_settings("legacy")

        self.assertEqual(loaded["max_interval"], 240)
        self.assertEqual(loaded["heartbeat_interval"], 240)
        self.assertEqual(loaded["default_worker_concurrency"], 2)
        self.assertFalse(loaded["restrict_to_created_agents"])
        self.assertEqual(loaded["autonomy_mode"], "dispatch_when_clear")
        self.assertEqual(loaded["wave_size_preference"], "small")
        self.assertEqual(
            loaded["same_agent_follow_up_preference"], "balanced"
        )
        self.assertEqual(loaded["digest_verbosity"], "balanced")
        self.assertEqual(loaded["escalation_style"], "note_then_ask")
        self.assertEqual(loaded["weaver_directory"], "")
        self.assertEqual(loaded["weaver_profile"], "")
        self.assertEqual(loaded["weaver_shell"], "")
        self.assertEqual(loaded["weaver_tab_color"], "")

    def test_weaver_task_log_roundtrip_and_group_rename(self):
        first_id = self.db.save_weaver_task_log_entry(
            {
                "group": "g",
                "task_id": "LOOM:1",
                "task_title": "Implement Worklog",
                "agent_id": "agent-1",
                "agent_name": "Worker One",
                "agent_slug": "worker-one",
                "agent_owned": True,
                "started_at": 10.0,
            }
        )
        second_id = self.db.save_weaver_task_log_entry(
            {
                "group": "g",
                "task_id": "LOOM:2",
                "task_title": "Review Worklog",
                "agent_id": "agent-2",
                "agent_name": "Worker Two",
                "agent_slug": "worker-two",
                "agent_owned": False,
                "started_at": 20.0,
            }
        )

        loaded = self.db.load_weaver_task_log("g", limit=10)

        self.assertEqual([entry["id"] for entry in loaded], [second_id, first_id])
        self.assertEqual(loaded[0]["task_id"], "LOOM:2")
        self.assertFalse(loaded[0]["agent_owned"])
        self.assertEqual(loaded[1]["agent_slug"], "worker-one")

        self.db.rename_weaver_task_log_group("g", "renamed")
        renamed = self.db.load_weaver_task_log("renamed", limit=10)

        self.assertEqual([entry["task_id"] for entry in renamed], ["LOOM:2", "LOOM:1"])
        self.assertEqual(self.db.load_weaver_task_log("g", limit=10), [])

        self.db.trim_weaver_task_log("renamed", limit=1)
        self.assertEqual(
            [entry["task_id"] for entry in self.db.load_weaver_task_log("renamed", limit=10)],
            ["LOOM:2"],
        )

    def test_playbook_candidates_roundtrip(self):
        candidate = {
            "id": "cand-1",
            "group": "g",
            "family_key": "g|feature/implement|feature|billing-dashboard",
            "status": "draft",
            "created_at": 10.0,
            "updated_at": 12.0,
            "name": "feature-implement-billing-dashboard",
            "root_action": "feature/implement",
            "labels": ["feature"],
            "normalized_task_family": "billing-dashboard",
            "entry_action": "feature/implement",
            "agent_template": "default",
            "workflow": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "workflow_shape": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "dispatch_sequence": [
                {
                    "action": "feature/implement",
                    "agent_template": "default",
                    "agent_type": "codex",
                    "uses_worktree": True,
                },
                {
                    "action": "feature/review",
                    "agent_template": "default",
                    "agent_type": "codex",
                    "uses_worktree": True,
                },
            ],
            "action_combination": ["feature/implement", "feature/review"],
            "constraints": {"worktree": True, "review_required": False},
            "evidence": {"quality_score": 0.91, "successful_runs": 3},
            "supporting_runs": [{"root_task_id": "root-1"}],
            "counterexamples": [{"root_task_id": "root-4", "reason": "blocked"}],
        }

        self.db.replace_playbook_candidates([candidate], group_name="g")

        loaded = self.db.load_playbook_candidates(group_name="g", limit=10)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], candidate["name"])
        self.assertEqual(loaded[0]["workflow"][1]["action"], "feature/review")
        self.assertTrue(loaded[0]["constraints"]["worktree"])
        self.assertEqual(loaded[0]["supporting_runs"][0]["root_task_id"], "root-1")

    def test_playbooks_roundtrip(self):
        playbook = {
            "id": "playbook-cand-1",
            "group": "g",
            "source_candidate_id": "cand-1",
            "status": "draft",
            "generated": True,
            "review_required": True,
            "created_at": 20.0,
            "updated_at": 21.0,
            "published_at": None,
            "discarded_at": None,
            "name": "feature-implement-billing-dashboard",
            "description": "Generated from Loom history. Review before publication.",
            "match": {
                "root_action": "feature/implement",
                "labels": ["feature"],
                "normalized_task_family": "billing-dashboard",
            },
            "entry_action": "feature/implement",
            "agent_template": "default",
            "workflow": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "constraints": {"worktree": True},
            "evidence": {"quality_score": 0.91},
            "publication_preview": {
                "generated": True,
                "review_state": "pending",
                "ready_to_publish": True,
            },
        }

        self.db.save_playbook(playbook)

        loaded = self.db.load_playbooks(group_name="g", status_filter="draft", limit=10)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["source_candidate_id"], "cand-1")
        self.assertTrue(loaded[0]["generated"])
        self.assertEqual(loaded[0]["match"]["normalized_task_family"], "billing-dashboard")
        self.assertTrue(loaded[0]["publication_preview"]["ready_to_publish"])

    def test_memory_entries_roundtrip_and_filters(self):
        self.db.save_memory_entry(
            {
                "id": "mem-1",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "task",
                "scope_ref": "task-1",
                "entry_type": "decision",
                "title": "Adopt SQLite memory",
                "content": "Use SQLite as the durable source of truth.",
                "pinned": True,
                "task_id": "task-1",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "retention_kind": "durable",
                "expires_at": None,
                "created_at": 10.0,
                "updated_at": 10.0,
            }
        )
        self.db.save_memory_entry(
            {
                "id": "mem-2",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "finding",
                "title": "Keep snapshots small",
                "content": "Do not mirror memory into full UI snapshots.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Reviewer",
                "retention_kind": "transient",
                "expires_at": 4_102_444_800.0,
                "created_at": 20.0,
                "updated_at": 20.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-1",
                "target_kind": "task",
                "target_ref": "task-1",
                "created_at": 11.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-1",
                "target_kind": "agent",
                "target_ref": "agent-1",
                "created_at": 12.0,
            }
        )
        self.db.save_memory_link(
            {
                "entry_id": "mem-2",
                "target_kind": "pipeline",
                "target_ref": "task-1",
                "created_at": 21.0,
            }
        )

        all_entries = self.db.load_memory_entries(group_name="g", limit=10)
        self.assertEqual([entry["id"] for entry in all_entries], ["mem-1", "mem-2"])
        self.assertEqual(
            [link["target_kind"] for link in all_entries[0]["links"]],
            ["task", "agent"],
        )

        pinned = self.db.load_memory_entries(
            group_name="g", pinned_only=True, limit=10
        )
        self.assertEqual([entry["id"] for entry in pinned], ["mem-1"])

        scoped = self.db.load_memory_entries(
            scope_kind="task", scope_ref="task-1", limit=10
        )
        self.assertEqual([entry["id"] for entry in scoped], ["mem-1"])

        searched = self.db.load_memory_entries(search="snapshots", limit=10)
        self.assertEqual([entry["id"] for entry in searched], ["mem-2"])

        linked = self.db.load_memory_entries(
            linked_target_kind="agent",
            linked_target_ref="agent-1",
            limit=10,
        )
        self.assertEqual([entry["id"] for entry in linked], ["mem-1"])

        self.db.set_memory_entry_pinned("mem-2", True, 30.0)
        entry = self.db.load_memory_entry("mem-2")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["retention_kind"], "durable")
        self.assertIsNone(entry["expires_at"])
        self.assertEqual(entry["updated_at"], 30.0)
        self.assertEqual(
            entry["links"],
            [
                {
                    "entry_id": "mem-2",
                    "target_kind": "pipeline",
                    "target_ref": "task-1",
                    "created_at": 21.0,
                }
            ],
        )

    def test_memory_entries_purge_expired_transient_rows_on_read(self):
        self.db.save_memory_entry(
            {
                "id": "mem-active",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "decision",
                "title": "Keep it",
                "content": "Durable decisions survive retention cleanup.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "retention_kind": "durable",
                "expires_at": None,
                "created_at": 10.0,
                "updated_at": 10.0,
            }
        )
        self.db.save_memory_entry(
            {
                "id": "mem-expired",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "note",
                "title": "Stale scratch note",
                "content": "Safe to remove.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Worker 2",
                "retention_kind": "transient",
                "expires_at": 15.0,
                "created_at": 11.0,
                "updated_at": 11.0,
            }
        )

        entries = self.db.load_memory_entries(group_name="g", limit=10, now=20.0)

        self.assertEqual([entry["id"] for entry in entries], ["mem-active"])
        self.assertIsNone(self.db.load_memory_entry("mem-expired", now=20.0))

    def test_init_upgrades_legacy_memory_entries_before_retention_indexes(self):
        legacy_path = Path(self.tmp.name) / "legacy-memory.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.executescript("""
            CREATE TABLE memory_entries (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL DEFAULT '',
                group_name TEXT NOT NULL DEFAULT '',
                scope_kind TEXT NOT NULL,
                scope_ref TEXT NOT NULL DEFAULT '',
                entry_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                task_id TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO memory_entries (
                id, project_key, group_name, scope_kind, scope_ref,
                entry_type, title, content, pinned, task_id,
                source_kind, source_id, source_name, created_at, updated_at
            ) VALUES (
                'mem-1', '/repo', 'g', 'group', 'g',
                'note', 'Legacy note', 'Migrated safely.', 0, '',
                'agent', 'agent-1', 'Worker', 1.0, 1.0
            );
        """)
        conn.commit()
        conn.close()

        upgraded = LoomDB(legacy_path)
        self.addCleanup(upgraded.close)

        upgraded.init()

        entry = upgraded.load_memory_entry("mem-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["retention_kind"], "durable")
        cols = upgraded._conn.execute(
            "PRAGMA table_info(memory_entries)"
        ).fetchall()
        self.assertIn("retention_kind", [row[1] for row in cols])

    def test_init_applies_kinds_schema_migration_once_with_backup(self):
        legacy_path = Path(self.tmp.name) / "legacy-kinds.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.execute("CREATE TABLE keep (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO keep (id) VALUES ('row-1')")
        conn.commit()
        conn.close()

        migrated = LoomDB(legacy_path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        backup_path = legacy_path.with_name("loom.db.pre-kinds.bak")
        joined_logs = "\n".join(cm.output)
        self.assertIn(
            f"migration: created pre-kinds backup at {backup_path}",
            joined_logs,
        )
        self.assertIn(
            f"migration: kinds schema applied (version=1, backup={backup_path})",
            joined_logs,
        )
        self.assertIn(
            "migration: no Weaver found, skipping engineer backfill",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds backfill applied (engineer=None, workers=0, tasks=0)",
            joined_logs,
        )
        self.assertTrue(backup_path.exists())
        self.assertGreater(backup_path.stat().st_size, 0)

        backup_conn = sqlite3.connect(str(backup_path))
        self.addCleanup(backup_conn.close)
        self.assertEqual(
            backup_conn.execute("SELECT id FROM keep").fetchone()[0],
            "row-1",
        )

        agent_cols = {
            row[1]: row
            for row in migrated._conn.execute("PRAGMA table_info(agents)").fetchall()
        }
        for col, default in {
            "kind": "''",
            "role": "''",
            "owner_engineer_id": "''",
            "hired_by_architect_id": "''",
            "persistent": "0",
        }.items():
            self.assertIn(col, agent_cols)
            self.assertEqual(str(agent_cols[col][4]), default)

        task_cols = {
            row[1]: row
            for row in migrated._conn.execute(
                "PRAGMA table_info(board_tasks)"
            ).fetchall()
        }
        for col in (
            "assigned_engineer_id",
            "created_by_architect_id",
            "suggested_action",
        ):
            self.assertIn(col, task_cols)
            self.assertEqual(str(task_cols[col][4]), "''")

        decisions_sql = migrated._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()[0]
        self.assertIn("CREATE TABLE decisions", decisions_sql)
        self.assertIn("architect_id TEXT NOT NULL DEFAULT ''", decisions_sql)
        self.assertIn("linked_engineer_ids TEXT NOT NULL DEFAULT '[]'", decisions_sql)
        self.assertIsNotNone(
            migrated._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='idx_decisions_architect'"
            ).fetchone()
        )

        version = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
        ).fetchone()[0]
        migrated_at = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_migrated_at'"
        ).fetchone()[0]
        self.assertEqual(version, "3")
        self.assertTrue(migrated_at)

        migrated.save_agent(
            AgentCell(
                id="agent-kinds",
                name="Kinds Worker",
                group="g",
                slug="kinds-worker",
            )
        )
        migrated.save_board_task(
            BoardTask(
                id="task-kinds",
                task="Kinds Task",
                slug="kinds-task",
                group="g",
            )
        )
        loaded = migrated.load_all()
        self.assertEqual(loaded["agents"]["agent-kinds"]["kind"], "")
        self.assertEqual(loaded["agents"]["agent-kinds"]["role"], "")
        self.assertEqual(
            loaded["agents"]["agent-kinds"]["owner_engineer_id"],
            "",
        )
        self.assertFalse(loaded["agents"]["agent-kinds"]["persistent"])
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["assigned_engineer_id"],
            "",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["created_by_architect_id"],
            "",
        )
        self.assertEqual(
            loaded["board_tasks"]["task-kinds"]["suggested_action"],
            "",
        )

        backup_mtime_ns = backup_path.stat().st_mtime_ns
        migrated.close()

        rerun = LoomDB(legacy_path)
        self.addCleanup(rerun.close)
        logger = logging.getLogger("loom")
        handler = logging.Handler()
        captured_messages = []
        handler.emit = lambda record: captured_messages.append(record.getMessage())
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            rerun.init()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertEqual(backup_path.stat().st_mtime_ns, backup_mtime_ns)
        self.assertFalse(
            any("pre-kinds backup" in msg for msg in captured_messages),
            captured_messages,
        )
        self.assertFalse(
            any("kinds schema applied" in msg for msg in captured_messages),
            captured_messages,
        )
        self.assertFalse(
            any("kinds backfill applied" in msg for msg in captured_messages),
            captured_messages,
        )

    def test_init_backfills_kinds_with_root_weaver_candidate(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-root.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="loom",
                    slug="coordinator",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker One",
                    group="loom",
                    slug="worker-one",
                    template="researcher",
                    created_by_weaver_id="root-1",
                ),
                AgentCell(
                    id="other-weaver",
                    name="Weaver",
                    group="alpha",
                    slug="weaver",
                ),
                AgentCell(
                    id="term-1",
                    name="Logs",
                    group="loom",
                    slug="loom:logs",
                    cell_type="terminal",
                ),
            ],
            tasks=[
                BoardTask(id="LOOM:1", task="Owned task", group="loom"),
                BoardTask(id="ALPHA:1", task="Unowned task", group="alpha"),
            ],
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=root-1, workers=2, tasks=2)",
            joined_logs,
        )

        engineer = migrated._conn.execute(
            "SELECT name, slug, kind, role, owner_engineer_id, persistent "
            "FROM agents WHERE id='root-1'"
        ).fetchone()
        self.assertEqual(engineer, ("Weaver-2", "weaver-2", "engineer", "", "", 1))

        worker = migrated._conn.execute(
            "SELECT kind, role, owner_engineer_id, hired_by_architect_id, persistent "
            "FROM agents WHERE id='worker-1'"
        ).fetchone()
        self.assertEqual(worker, ("worker", "researcher", "root-1", "", 0))

        terminal = migrated._conn.execute(
            "SELECT kind, role, owner_engineer_id, persistent "
            "FROM agents WHERE id='term-1'"
        ).fetchone()
        self.assertEqual(terminal, ("terminal", "", "", 0))

        task_rows = migrated._conn.execute(
            "SELECT id, assigned_engineer_id, created_by_architect_id, suggested_action "
            "FROM board_tasks ORDER BY id"
        ).fetchall()
        self.assertEqual(
            task_rows,
            [
                ("ALPHA:1", "", "", ""),
                ("LOOM:1", "", "", ""),
            ],
        )
        version = migrated._conn.execute(
            "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
        ).fetchone()[0]
        self.assertEqual(version, "3")

        migrated.close()

        rerun = LoomDB(path)
        self.addCleanup(rerun.close)
        logger = logging.getLogger("loom")
        handler = logging.Handler()
        captured = []
        handler.emit = lambda record: captured.append(record.getMessage())
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            rerun.init()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        self.assertFalse(
            any("kinds backfill applied" in msg for msg in captured),
            captured,
        )

    def test_init_backfills_configured_loom_weaver_without_heuristic_match(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-configured-weaver.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="loom",
                    slug="coordinator",
                ),
                AgentCell(
                    id="agent-1",
                    name="Planner",
                    group="alpha",
                    slug="planner",
                    template="planner",
                ),
            ],
            tasks=[
                BoardTask(id="LOOM:1", task="Loom task", group="loom"),
                BoardTask(id="ALPHA:1", task="Alpha task", group="alpha"),
            ],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="root-1"),
            },
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=root-1, workers=1, tasks=2)",
            joined_logs,
        )
        self.assertNotIn("no Weaver found", joined_logs)
        self.assertEqual(
            migrated._conn.execute(
                "SELECT name, slug, kind, persistent FROM agents WHERE id='root-1'"
            ).fetchone(),
            ("Weaver", "weaver", "engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='agent-1'"
            ).fetchone(),
            ("worker", "planner", "", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", ""),
                ("LOOM:1", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_backfills_existing_agents_without_promoting_when_no_weaver_found(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-no-weaver.db",
            agents=[
                AgentCell(
                    id="agent-1",
                    name="Planner",
                    group="alpha",
                    slug="planner",
                    template="planner",
                ),
                AgentCell(
                    id="agent-2",
                    name="Coordinator",
                    group="loom",
                    slug="coordinator",
                    template="default",
                ),
            ],
            tasks=[BoardTask(id="ALPHA:1", task="Task", group="alpha")],
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: no Weaver found, skipping engineer backfill",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds backfill applied (engineer=None, workers=2, tasks=1)",
            joined_logs,
        )

        rows = migrated._conn.execute(
            "SELECT id, kind, role, owner_engineer_id, persistent "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("agent-1", "worker", "planner", "", 0),
                ("agent-2", "worker", "default", "", 0),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "ALPHA:1")
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_skips_ambiguous_weaver_backfill_without_override(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-ambiguous.db",
            agents=[
                AgentCell(
                    id="weaver-a",
                    name="Weaver Alpha",
                    group="loom",
                    slug="weaver-alpha",
                ),
                AgentCell(
                    id="weaver-b",
                    name="Bob",
                    group="loom",
                    slug="bob",
                    template="weaver",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="loom",
                    slug="worker",
                    created_by_weaver_id="weaver-a",
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Task", group="loom")],
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="ERROR") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn("multiple Weaver candidates found", joined_logs)
        self.assertIn("weaver-a", joined_logs)
        self.assertIn("weaver-b", joined_logs)

        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "1",
        )
        rows = migrated._conn.execute(
            "SELECT id, kind, role, owner_engineer_id, persistent "
            "FROM agents ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("weaver-a", "", "", "", 0),
                ("weaver-b", "", "", "", 0),
                ("worker-1", "", "", "", 0),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "LOOM:1")
            ).fetchone()[0],
            "",
        )

    def test_init_uses_configured_loom_weaver_to_disambiguate_candidates(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-configured-disambiguation.db",
            agents=[
                AgentCell(
                    id="weaver-a",
                    name="Weaver Alpha",
                    group="loom",
                    slug="weaver-alpha",
                ),
                AgentCell(
                    id="weaver-b",
                    name="Bob",
                    group="loom",
                    slug="bob",
                    template="weaver",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="loom",
                    slug="worker",
                    template="researcher",
                    created_by_weaver_id="weaver-b",
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Task", group="loom")],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="weaver-b"),
            },
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=weaver-b, workers=2, tasks=1)",
            joined_logs,
        )
        self.assertNotIn("multiple Weaver candidates found", joined_logs)
        self.assertEqual(
            migrated._conn.execute(
                "SELECT name, slug, kind, persistent FROM agents WHERE id='weaver-b'"
            ).fetchone(),
            ("Weaver", "weaver", "engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='weaver-a'"
            ).fetchone(),
            ("worker", "", "", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='LOOM:1'"
            ).fetchone()[0],
            "weaver-b",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_uses_env_override_for_ambiguous_weaver_backfill(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-override.db",
            agents=[
                AgentCell(
                    id="weaver-a",
                    name="Weaver Alpha",
                    group="loom",
                    slug="weaver-alpha",
                ),
                AgentCell(
                    id="weaver-b",
                    name="Bob",
                    group="loom",
                    slug="bob",
                    template="weaver",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="loom",
                    slug="worker",
                    template="researcher",
                    created_by_weaver_id="weaver-b",
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Task", group="loom")],
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with mock.patch.dict("os.environ", {"LOOM_MIGRATE_WEAVER_ID": "weaver-b"}):
            with self.assertLogs("loom", level="INFO") as cm:
                migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: kinds backfill applied (engineer=weaver-b, workers=2, tasks=1)",
            joined_logs,
        )

        engineer = migrated._conn.execute(
            "SELECT name, slug, kind, persistent FROM agents WHERE id='weaver-b'"
        ).fetchone()
        self.assertEqual(engineer, ("Weaver", "weaver", "engineer", 1))
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, role, owner_engineer_id, persistent "
                "FROM agents WHERE id='worker-1'"
            ).fetchone(),
            ("worker", "researcher", "weaver-b", 0),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='task-1'"
                .replace("task-1", "LOOM:1")
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_backfills_task_assignments_from_group_settings_weaver(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-task-assignment.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Coordinator",
                    group="loom",
                    slug="coordinator",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                    template="researcher",
                ),
            ],
            tasks=[
                BoardTask(id="LOOM:1", task="Loom task", group="loom"),
                BoardTask(id="LOOM:2", task="Second loom task", group="loom"),
                BoardTask(id="ALPHA:1", task="Alpha task", group="alpha"),
            ],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="root-1"),
            },
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)
        migrated.init()

        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", ""),
                ("LOOM:1", "root-1"),
                ("LOOM:2", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )

    def test_init_ignores_stale_group_settings_weaver_without_engineer(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-stale-group-weaver.db",
            agents=[
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Loom task", group="loom")],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="stale-id"),
            },
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        self.assertIn(
            "migration: ignoring stale weaver_agent_id='stale-id' for group='loom'",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='LOOM:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_ignores_stale_group_settings_weaver_with_heuristic_engineer(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-stale-group-weaver-heuristic.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Weaver",
                    group="loom",
                    slug="weaver",
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="loom",
                    slug="worker",
                    created_by_weaver_id="root-1",
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Loom task", group="loom")],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="stale-id"),
            },
        )

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="WARNING") as cm:
            migrated.init()

        self.assertIn(
            "migration: ignoring stale weaver_agent_id='stale-id' for group='loom'",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT kind, persistent FROM agents WHERE id='root-1'"
            ).fetchone(),
            ("engineer", 1),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='LOOM:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key='schema_kinds_migration_version'"
            ).fetchone()[0],
            "3",
        )

    def test_init_fixes_version2_unassigned_tasks_from_group_settings(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-fixup.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Weaver",
                    group="loom",
                    slug="weaver",
                    kind="engineer",
                    persistent=True,
                ),
                AgentCell(
                    id="worker-1",
                    name="Worker",
                    group="alpha",
                    slug="worker",
                    template="researcher",
                    kind="worker",
                    role="researcher",
                ),
            ],
            tasks=[
                BoardTask(id="LOOM:1", task="Needs fixup", group="loom"),
                BoardTask(
                    id="ALPHA:1",
                    task="Already assigned",
                    group="alpha",
                    assigned_engineer_id="explicit-owner",
                ),
            ],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="root-1"),
            },
        )
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "DELETE FROM meta WHERE key='schema_kinds_task_assignment_fixup_applied'"
        )
        conn.execute(
            "UPDATE agents SET kind='engineer', persistent=1 WHERE id='root-1'"
        )
        conn.execute(
            "UPDATE agents SET kind='worker', role='researcher' WHERE id='worker-1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='explicit-owner' "
            "WHERE id='ALPHA:1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='' WHERE id='LOOM:1'"
        )
        conn.commit()
        conn.close()

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        self.assertIn(
            "migration: kinds task assignment fixup applied (updated=1)",
            "\n".join(cm.output),
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT id, assigned_engineer_id FROM board_tasks ORDER BY id"
            ).fetchall(),
            [
                ("ALPHA:1", "explicit-owner"),
                ("LOOM:1", "root-1"),
            ],
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )

    def test_init_fixup_ignores_stale_group_settings_weaver_for_unassigned_tasks(self):
        path = self._seed_stage1a_db(
            "kinds-backfill-fixup-stale-group-weaver.db",
            agents=[
                AgentCell(
                    id="root-1",
                    name="Weaver",
                    group="loom",
                    slug="weaver",
                    kind="engineer",
                    persistent=True,
                ),
            ],
            tasks=[BoardTask(id="LOOM:1", task="Needs fixup", group="loom")],
            group_settings={
                "loom": GroupSettings(weaver_agent_id="stale-id"),
            },
        )
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE meta SET value='2' WHERE key='schema_kinds_migration_version'"
        )
        conn.execute(
            "DELETE FROM meta WHERE key='schema_kinds_task_assignment_fixup_applied'"
        )
        conn.execute(
            "UPDATE agents SET kind='engineer', persistent=1 WHERE id='root-1'"
        )
        conn.execute(
            "UPDATE board_tasks SET assigned_engineer_id='' WHERE id='LOOM:1'"
        )
        conn.commit()
        conn.close()

        migrated = LoomDB(path)
        self.addCleanup(migrated.close)

        with self.assertLogs("loom", level="INFO") as cm:
            migrated.init()

        joined_logs = "\n".join(cm.output)
        self.assertIn(
            "migration: ignoring stale weaver_agent_id='stale-id' for group='loom'",
            joined_logs,
        )
        self.assertIn(
            "migration: kinds task assignment fixup applied (updated=0)",
            joined_logs,
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT assigned_engineer_id FROM board_tasks WHERE id='LOOM:1'"
            ).fetchone()[0],
            "",
        )
        self.assertEqual(
            migrated._conn.execute(
                "SELECT value FROM meta WHERE key=?",
                ("schema_kinds_task_assignment_fixup_applied",),
            ).fetchone()[0],
            "1",
        )
