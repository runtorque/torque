import tempfile
import unittest
from pathlib import Path
import json

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
                "enqueued_at": "2026-04-07T09:00:00+00:00",
            }
        ])
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
                "paused": True,
                "custom_instructions": "Focus on regressions.",
                "pending_question": "Need approval",
                "pending_note": "FYI: release notes are ready",
                "pending_note_kind": "note",
                "enabled_events": ["task_completed"],
                "weaver_provider": "codex",
                "weaver_boot_command": "codex --model gpt-5",
            },
        )
        first = self.db.save_journal_entry("g", 10.0, "decision", "Ship it")
        second = self.db.save_journal_entry("g", 20.0, "plan", "Add tests")

        loaded = self.db.load_weaver_settings("g")
        self.assertEqual(loaded["pending_question"], "Need approval")
        self.assertEqual(loaded["pending_note"], "FYI: release notes are ready")
        self.assertEqual(loaded["pending_note_kind"], "note")
        self.assertEqual(loaded["enabled_events"], ["task_completed"])
        self.assertEqual(loaded["heartbeat_interval"], 180)
        self.assertEqual(loaded["weaver_boot_command"], "codex --model gpt-5")

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
