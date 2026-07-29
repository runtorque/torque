import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.db import TorqueDB
from torque.doctor import build_doctor_report_for_db, format_doctor_report

install_aiohttp_stub()
from torque.state import AgentCell, BoardTask, GlobalSettings, GroupSettings


class TorqueDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "torque.db"
        self.db = TorqueDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

    def _home_dir(self) -> Path:
        home = Path(self.tmp.name) / "home"
        (home / ".torque" / "agents").mkdir(parents=True, exist_ok=True)
        return home

    def _save_engineer(self, engineer_id="engineer-1", name="Engineer"):
        self.db.save_agent(
            AgentCell(
                id=engineer_id,
                name=name,
                group="torque",
                slug=name.lower(),
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )

    def _save_architect(self, architect_id="arch-1", name="productmind", **overrides):
        self.db.save_agent(
            AgentCell(
                id=architect_id,
                name=name,
                group="torque",
                slug=name.lower(),
                cell_type="agent",
                kind="architect",
                persistent=True,
                **overrides,
            )
        )

    def _save_worker(self, worker_id: str, name: str, **overrides):
        self.db.save_agent(
            AgentCell(
                id=worker_id,
                name=name,
                group="torque",
                slug=name.lower().replace(" ", "-"),
                cell_type="agent",
                kind="worker",
                **overrides,
            )
        )

    def _add_legacy_kinds_columns(self):
        self.db._conn.execute(
            "ALTER TABLE agents ADD COLUMN template TEXT NOT NULL DEFAULT ''"
        )
        self.db._conn.execute(
            "ALTER TABLE agents ADD COLUMN created_by_engineer_id TEXT NOT NULL DEFAULT ''"
        )
        self.db._conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN engineer_owner_id TEXT NOT NULL DEFAULT ''"
        )

    def test_pty_supervisor_check_status_matrix(self):
        from torque.doctor import _check_pty_supervisor_reachable
        # Present + unreachable = down/wedged => fail.
        self.assertEqual(
            _check_pty_supervisor_reachable(
                {"pty_supervisor": {"socket_present": True, "reachable": False}}
            )["status"],
            "fail",
        )
        # Present + reachable => pass.
        self.assertEqual(
            _check_pty_supervisor_reachable(
                {"pty_supervisor": {"socket_present": True, "reachable": True}}
            )["status"],
            "pass",
        )
        # No socket (supervisor not running) is not a failure.
        self.assertEqual(
            _check_pty_supervisor_reachable(
                {"pty_supervisor": {"socket_present": False, "reachable": False}}
            )["status"],
            "pass",
        )

    def test_pty_supervisor_section_absent_socket(self):
        from torque.doctor import _collect_pty_supervisor_section
        with tempfile.TemporaryDirectory() as d:
            sec = _collect_pty_supervisor_section(Path(d) / "torque.db")
            self.assertFalse(sec["socket_present"])
            self.assertFalse(sec["reachable"])
            self.assertIsNone(sec["pid"])

    def test_multiprocessing_children_section_parses_spawn_rows(self):
        from torque.doctor import _collect_multiprocessing_children_section

        ps_output = "\n".join([
            " 123  100  28656  3:22.46 /opt/python -c from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=30, pipe_handle=38) --multiprocessing-fork",
            " 124  100  18400  3:22.46 /opt/python -c from multiprocessing.resource_tracker import main;main(33)",
            " 125  100   1024  0:00.01 /bin/zsh",
        ])

        sec = _collect_multiprocessing_children_section(ps_output=ps_output)

        self.assertTrue(sec["available"])
        self.assertEqual(sec["count"], 2)
        self.assertEqual(sec["spawn_worker_count"], 1)
        self.assertEqual(sec["resource_tracker_count"], 1)
        self.assertEqual(sec["total_rss_bytes"], (28656 + 18400) * 1024)
        self.assertEqual(sec["max_rss_bytes"], 28656 * 1024)

    def test_stuck_input_sessions_warning_and_render(self):
        from torque.doctor import (
            _warn_stuck_input_sessions,
            build_doctor_report_for_db,
            format_doctor_report,
        )
        # No open breakers => no warning.
        self.assertIsNone(_warn_stuck_input_sessions({"pty_supervisor": {}}))
        # Open breakers => warning carrying count + session ids.
        warning = _warn_stuck_input_sessions(
            {"pty_supervisor": {"open_write_breakers": {"sess-aaaa1111": 12.0}}}
        )
        self.assertEqual(warning["name"], "stuck_input_sessions")
        self.assertEqual(warning["details"]["count"], 1)

        # Render: inject the runtime fields the daemon adds, then format.
        report = build_doctor_report_for_db(self.db_path)
        report["pty_supervisor"]["open_write_breakers"] = {"sess-aaaa1111": 12.0}
        report["pty_supervisor"]["stuck_sessions"] = 1
        report.setdefault("warnings", []).append(warning)
        text = format_doctor_report(report)
        self.assertIn("stuck_input_sessions:", text)
        self.assertIn("open input-write breaker", text)

    def test_pty_supervisor_text_includes_live_health_fields(self):
        report = build_doctor_report_for_db(self.db_path)
        report["pty_supervisor"]["health"] = {
            "state": "down",
            "connected": False,
            "time_since_last_successful_op": 12.5,
        }

        text = format_doctor_report(report)

        self.assertIn("state:                          down", text)
        self.assertIn("connected:                      false", text)
        self.assertIn("time_since_last_successful_op:  12.5", text)

    def test_build_doctor_report_warns_for_ai_enabled_missing_optional_deps(self):
        home = self._home_dir()
        self.db.save_global_settings(GlobalSettings(ai_enabled=True))

        with mock.patch.dict(os.environ, {"HOME": str(home)}), \
             mock.patch(
                 "torque.doctor.ai_deps.embeddings_dependency_status",
                 return_value="missing",
             ), \
             mock.patch(
                 "torque.doctor.ai_deps.missing_ai_dependency_packages",
                 return_value=["sentence-transformers", "sqlite-vec"],
             ):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        warning_names = [warning["name"] for warning in report["warnings"]]
        self.assertEqual(report["result"], "pass")
        self.assertNotIn("ai_optional_deps_missing", report["failed_checks"])
        self.assertIn("ai_optional_deps_missing", warning_names)
        self.assertEqual(report["ai"]["embeddings_dependency"]["status"], "missing")
        self.assertIn("[ai]", rendered)
        self.assertIn("embeddings_dependency_status:   missing", rendered)
        self.assertIn(
            "AI is enabled but optional embedding dependencies are missing: "
            "sentence-transformers, sqlite-vec (run make ai-deps)",
            rendered,
        )

    def test_build_doctor_report_warns_for_ai_index_rebuild_and_mismatch(self):
        home = self._home_dir()
        self.db.save_global_settings(GlobalSettings(
            ai_enabled=True,
            ai_embedding_model="model-b",
        ))
        self.db.ai_update_index_state(
            desired_model_id="model-b",
            active_model_id="model-a",
            active_dims=3,
            status="rebuild_pending",
            rebuild_required=True,
            rebuild_reason="embedding_model_change",
        )
        conn = self.db.open_ai_index_connection()
        try:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS ai_embedding_vec "
                    "(rowid INTEGER PRIMARY KEY, embedding TEXT NOT NULL)"
                )
                self.db.ai_upsert_embedding_source({
                    "source_key": "task:TORQUE:1",
                    "source_type": "task",
                    "source_id": "TORQUE:1",
                    "content_hash": "hash-a",
                }, conn=conn, commit=False)
                self.db.ai_replace_source_chunks(
                    "task:TORQUE:1",
                    [{
                        "chunk_index": 0,
                        "text": "hello",
                        "chunk_hash": "chunk-a",
                        "vector": [1, 2, 3, 4],
                    }],
                    conn=conn,
                    model_id="model-old",
                    dims=4,
                    content_hash="hash-a",
                )
        finally:
            conn.close()

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        warning_names = [warning["name"] for warning in report["warnings"]]
        self.assertEqual(report["result"], "pass")
        self.assertIn("ai_index_rebuild_pending", warning_names)
        self.assertIn("ai_index_chunk_model_mismatch", warning_names)
        self.assertEqual(report["ai"]["index_counts"]["model_mismatch_chunks"], 1)
        self.assertIn("index_model_mismatch_chunks:    1", rendered)
        self.assertIn("AI vector index rebuild pending", rendered)

    def test_build_doctor_report_flags_alias_missing_canonical_collision(self):
        home = self._home_dir()
        self._save_engineer()
        self.db.save_board_task(
            BoardTask(
                id="TORQUE:51",
                task="Archived header task",
                slug="archived-header-task",
                group="torque",
                lane="Archived",
                archived_at="2026-04-07T00:00:00+00:00",
                assigned_engineer_id="engineer-1",
            )
        )
        self.db.save_task_id_alias("TORQUE:51", "bcf3a475")

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        aliases = report["task_aliases"]
        self.assertEqual(aliases["total"], 1)
        self.assertEqual(aliases["literal_collision_count"], 1)
        self.assertEqual(aliases["archived_literal_collision_count"], 1)
        self.assertEqual(aliases["missing_canonical_count"], 1)
        self.assertEqual(
            aliases["missing_canonical"][0]["legacy_id"],
            "TORQUE:51",
        )
        self.assertEqual(
            aliases["strategy"],
            "alias_precedence_archived_literals_hidden",
        )
        warning_names = [warning["name"] for warning in report["warnings"]]
        self.assertIn("task_aliases_missing_canonical", warning_names)
        self.assertIn("missing_canonical:             1", rendered)
        self.assertIn("TORQUE:51->bcf3a475", rendered)

    def test_build_doctor_report_warns_when_no_engineer_exists(self):
        home = self._home_dir()
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Unassigned task",
                slug="unassigned-task",
                group="g",
            )
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            report["warnings"],
            [
                {
                    "name": "no_engineers",
                    "status": "warn",
                    "details": {
                        "count": 0,
                        "hint": (
                            "no engineer exists; create one from the Agent panel "
                            "before using engineer MCP tools"
                        ),
                    },
                }
            ],
        )
        self.assertEqual(report["tasks"]["unassigned"], 1)
        self.assertEqual(report["tasks"]["unassigned_when_engineer_present"], 0)
        self.assertEqual(report["engineers"]["total"], 0)
        self.assertEqual(report["roles"]["roles_file_count"], 0)
        self.assertEqual(report["stage_6_cleanup"]["legacy_template_files_ignored"], 0)
        self.assertFalse(report["stage_6_cleanup"]["legacy_columns_present"])
        self.assertFalse(report["stage_6_cleanup"]["engineer_tool_aliases_present"])
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "no engineer exists; create one from the Agent panel before using engineer MCP tools",
            rendered,
        )
        self.assertIn("roles_dir:                      ~/.torque/roles (0 files)", rendered)
        self.assertIn("[stage_6_cleanup]", rendered)
        self.assertIn("legacy_template_files_ignored:  0", rendered)
        self.assertIn("[architects]", rendered)
        self.assertIn("[pending_hires]", rendered)

    def test_build_doctor_report_warns_for_empty_kind_agent_with_task_history(self):
        home = self._home_dir()
        self._save_engineer(engineer_id="engineer-1", name="Courier")
        self.db.save_agent(
            AgentCell(
                id="empty-worker",
                name="Empty Worker",
                group="torque",
                slug="empty-worker",
                cell_type="agent",
                kind="",
                tasks_dispatched=1,
                persistent=False,
            )
        )
        self.db.save_agent_history({
            "id": "empty-worker",
            "name": "Empty Worker",
            "slug": "empty-worker",
            "group": "torque",
            "agent_type": "codex",
            "template": "",
            "created_at": time.time(),
            "total_tasks": 1,
            "status": "active",
        })

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(
            report["agents"]["empty_kind_with_task_history_count"],
            1,
        )
        warning = next(
            w for w in report["warnings"]
            if w["name"] == "empty_kind_agents_with_task_history"
        )
        self.assertEqual(warning["details"]["count"], 1)
        self.assertEqual(warning["details"]["agents"][0]["id"], "empty-worker")
        self.assertIn(
            "agent rows with kind='' have task history",
            rendered,
        )
        self.assertIn("empty-worker", rendered)

    def test_build_doctor_report_passes_for_fully_assigned_engineer_db(self):
        home = self._home_dir()
        self.db_path.with_name("torque.db.pre-kinds.bak").write_bytes(b"backup")

        self.db.save_agent(
            AgentCell(
                id="engineer-1",
                name="Engineer",
                group="torque",
                slug="engineer",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_agent(
            AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                slug="worker",
                cell_type="agent",
                template="researcher",
                role="researcher",
                created_by_engineer_id="engineer-1",
                owner_engineer_id="engineer-1",
                kind="worker",
            )
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Assigned task",
                slug="assigned-task",
                group="g",
                assigned_engineer_id="engineer-1",
            )
        )
        self.db.save_group_settings(
            "g",
            GroupSettings(engineer_agent_id="engineer-1"),
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["failed_checks"], [])
        self.assertGreaterEqual(
            report["migration"]["schema_kinds_migration_version"],
            4,
        )
        self.assertTrue(report["migration"]["backup_exists"])
        self.assertEqual(report["agents"]["total"], 2)
        self.assertEqual(report["agents"]["engineer"], 1)
        self.assertEqual(report["agents"]["engineer_name"], "Engineer")
        self.assertEqual(report["engineers"]["total"], 1)
        self.assertEqual(
            report["engineers"]["engineers"],
            [
                {
                    "id": "engineer-1",
                    "name": "Engineer",
                    "slug": "engineer",
                    "persistent": 1,
                    "worker_count": 1,
                    "task_count": 1,
                    "specializations": [],
                    "specialization_display": "generalist",
                }
            ],
        )
        self.assertEqual(report["agents"]["worker"], 1)
        self.assertEqual(report["agents"]["unmigrated"], 0)
        self.assertEqual(
            report["tasks"],
            {
                "total": 1,
                "assigned": 1,
                "unassigned": 0,
                "unassigned_when_engineer_present": 0,
            },
        )
        self.assertEqual(report["drift"]["agents_template_role"], 0)
        self.assertEqual(
            report["drift"]["agents_created_by_engineer_owner_engineer"],
            0,
        )
        self.assertEqual(
            [warning["name"] for warning in report["warnings"]],
            ["engineer_generalist_specialization"],
        )
        self.assertEqual(report["runtime_locations"]["data_dir_kind"], "custom")
        self.assertEqual(
            report["runtime_locations"]["primary_runtime_python"],
            str(home / ".torque" / "runtime" / "venv" / "bin" / "python"),
        )
        self.assertEqual(report["roles"]["roles_file_count"], 0)
        self.assertEqual(report["stage_6_cleanup"]["legacy_template_files_ignored"], 0)
        self.assertFalse(report["stage_6_cleanup"]["legacy_columns_present"])
        self.assertFalse(report["stage_6_cleanup"]["engineer_tool_aliases_present"])
        self.assertIn("Torque doctor — kinds refactor", rendered)
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn("[engineers]", rendered)
        self.assertIn("[architects]", rendered)
        self.assertIn("[pending_hires]", rendered)
        self.assertIn("[runtime_locations]", rendered)
        self.assertIn("[stage_6_cleanup]", rendered)
        self.assertNotIn("default (engineer_* routing)", rendered)

    def test_doctor_warns_when_reading_legacy_toolbelt_data_dir(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            db_path = (
                home
                / "Library/Application Support/iTerm2/Scripts/torque/torque/torque.db"
            )
            db_path.parent.mkdir(parents=True)
            db = TorqueDB(db_path)
            db.init()
            self.addCleanup(db.close)
            db.save_agent(
                AgentCell(
                    id="engineer-legacy",
                    name="Engineer",
                    group="torque",
                    slug="engineer",
                    cell_type="agent",
                    kind="engineer",
                    persistent=True,
                )
            )

            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                report = build_doctor_report_for_db(db_path)
                rendered = format_doctor_report(report)

        self.assertEqual(
            report["runtime_locations"]["data_dir_kind"],
            "legacy_toolbelt",
        )
        warning_names = {warning["name"] for warning in report["warnings"]}
        self.assertIn("legacy_toolbelt_data_dir", warning_names)
        self.assertIn("legacy Toolbelt data", rendered)

    def test_doctor_warns_on_legacy_appsupport_python_runtime(self):
        home = self._home_dir()
        self._save_engineer()
        legacy_python = (
            home
            / "Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/3.14.0/bin/python3"
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(
                self.db_path,
                runtime_python=str(legacy_python),
            )
            rendered = format_doctor_report(report)

        runtime = report["runtime_locations"]
        self.assertEqual(runtime["runtime_python"], str(legacy_python))
        self.assertEqual(runtime["runtime_python_kind"], "legacy_appsupport")
        warning_names = {warning["name"] for warning in report["warnings"]}
        self.assertIn("legacy_appsupport_python_runtime", warning_names)
        self.assertIn("legacy iTerm2/AppSupport Python", rendered)

    def test_build_doctor_report_includes_zero_architect_and_pending_hire_sections(self):
        home = self._home_dir()
        self._save_engineer()

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["architects"]["total"], 0)
        self.assertEqual(report["architects"]["architects"], [])
        self.assertEqual(
            report["pending_hires"],
            {
                "pending": 0,
                "approved_recent": 0,
                "rejected_recent": 0,
                "stale_pending": 0,
                "stale_pending_hires": [],
            },
        )
        self.assertIn("[architects]", rendered)
        self.assertIn("total:             0", rendered)
        self.assertIn("[pending_hires]", rendered)
        self.assertIn("pending:                  0", rendered)
        self.assertIn("approved:                 0 (in the last 7 days)", rendered)
        self.assertIn("rejected:                 0 (in the last 7 days)", rendered)
        self.assertIn("stale_pending (>24h):     0", rendered)
        self.assertEqual(
            report["worktrees"],
            {
                "total_worker_branches": 0,
                "namespaced": 0,
                "legacy": 0,
                "nonconforming": 0,
                "nonconforming_branches": [],
                "isolation_guard_repos": [],
                "isolation_guard_missing": [],
            },
        )
        self.assertIn("[worktrees]", rendered)
        self.assertIn("total_worker_branches: 0", rendered)
        self.assertIn("namespaced (stage 5):  0", rendered)
        self.assertIn("legacy (pre-stage-5):  0", rendered)
        self.assertIn("nonconforming:         0", rendered)

    def test_doctor_advises_that_empty_specialization_engineers_are_generalists(self):
        self._save_engineer("eng-forge", "Forge")

        report = build_doctor_report_for_db(self.db_path)
        warnings = {
            warning["name"]: warning for warning in report["warnings"]
        }

        self.assertEqual(report["result"], "pass")
        self.assertNotIn("engineer_generalist_specialization", report["failed_checks"])
        warning = warnings["engineer_generalist_specialization"]
        self.assertEqual(warning["status"], "warn")
        self.assertEqual(warning["details"]["engineers"][0]["id"], "eng-forge")
        self.assertIn("lowest-preference", warning["details"]["hint"])

    def test_build_doctor_report_accepts_custom_worker_branch_names(self):
        home = self._home_dir()
        self._save_engineer("eng-alice", "Alice")
        self._save_worker(
            "worker-custom",
            "Worker Custom",
            owner_engineer_id="eng-alice",
            worktree_branch="torque/alice/feature-api-v2-face123",
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            report["worktrees"],
            {
                "total_worker_branches": 1,
                "namespaced": 1,
                "legacy": 0,
                "nonconforming": 0,
                "nonconforming_branches": [],
                "isolation_guard_repos": [],
                "isolation_guard_missing": [],
            },
        )
        self.assertNotIn(
            "nonconforming_worker_worktree_branches",
            {warning.get("name", "") for warning in report["warnings"]},
        )
        self.assertIn("[worktrees]", rendered)
        self.assertIn("total_worker_branches: 1", rendered)
        self.assertIn("namespaced (stage 5):  1", rendered)
        self.assertIn("legacy (pre-stage-5):  0", rendered)
        self.assertIn("nonconforming:         0", rendered)

    def test_build_doctor_report_warns_for_nonconforming_worker_branch_names(self):
        home = self._home_dir()
        self._save_engineer("eng-alice", "Alice")
        self._save_worker(
            "worker-namespaced",
            "Worker Namespaced",
            owner_engineer_id="eng-alice",
            worktree_branch="torque/alice/worker-namespaced-a1b2c3",
        )
        self._save_worker(
            "worker-legacy",
            "Worker Legacy",
            owner_engineer_id="eng-alice",
            worktree_branch="torque/worker-legacy-deadbee",
        )
        self._save_worker(
            "worker-bad",
            "Worker Bad",
            owner_engineer_id="eng-alice",
            worktree_branch="torque/alice/worker-bad",
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            report["worktrees"],
            {
                "total_worker_branches": 3,
                "namespaced": 1,
                "legacy": 1,
                "nonconforming": 1,
                "nonconforming_branches": [
                    {
                        "id": "worker-bad",
                        "name": "Worker Bad",
                        "slug": "worker-bad",
                        "branch": "torque/alice/worker-bad",
                    }
                ],
                "isolation_guard_repos": [],
                "isolation_guard_missing": [],
            },
        )
        self.assertIn(
            {
                "name": "nonconforming_worker_worktree_branches",
                "status": "warn",
                "details": {
                    "count": 1,
                    "branches": [
                        {
                            "id": "worker-bad",
                            "name": "Worker Bad",
                            "slug": "worker-bad",
                            "branch": "torque/alice/worker-bad",
                        }
                    ],
                },
            },
            report["warnings"],
        )
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn("[worktrees]", rendered)
        self.assertIn("total_worker_branches: 3", rendered)
        self.assertIn("namespaced (stage 5):  1", rendered)
        self.assertIn("legacy (pre-stage-5):  1", rendered)
        self.assertIn("nonconforming:         1", rendered)
        self.assertIn(
            "worker worktree branches do not match stage-5 or legacy naming: torque/alice/worker-bad",
            rendered,
        )

    def test_build_doctor_report_warns_when_isolation_guard_missing(self):
        # A worker rooted in a real repo without Torque's pre-commit guard
        # should surface the worktree-isolation-guard warning (TORQUE:580).
        home = self._home_dir()
        repo_dir = Path(self.tmp.name) / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "-C", str(repo_dir), "init", "-b", "main"],
                       check=True, capture_output=True)
        self.assertFalse((repo_dir / ".git" / "hooks" / "pre-commit").exists())

        self._save_engineer("eng-alice", "Alice")
        self._save_worker(
            "worker-guarded",
            "Worker Guarded",
            owner_engineer_id="eng-alice",
            worktree_branch="torque/alice/worker-guarded-a1b2c3",
            worktree_repo_root=str(repo_dir),
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        worktrees = report["worktrees"]
        self.assertEqual(worktrees["isolation_guard_missing"], [str(repo_dir)])
        self.assertEqual(
            worktrees["isolation_guard_repos"],
            [{"repo_root": str(repo_dir), "installed": False}],
        )
        warning_names = {w.get("name", "") for w in report["warnings"]}
        self.assertIn("worktree_isolation_guard_missing", warning_names)
        self.assertIn("isolation_guard_missing: 1", rendered)

        # Once the guard is installed, the warning clears.
        from torque.worktree import ensure_worktree_isolation_guard
        ensure_worktree_isolation_guard(str(repo_dir))
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report2 = build_doctor_report_for_db(self.db_path)
        self.assertEqual(report2["worktrees"]["isolation_guard_missing"], [])
        self.assertNotIn(
            "worktree_isolation_guard_missing",
            {w.get("name", "") for w in report2["warnings"]},
        )

    def test_build_doctor_report_counts_architect_decisions_and_hired_engineers(self):
        home = self._home_dir()
        self._save_engineer()
        self._save_architect()
        self.db.save_agent(
            AgentCell(
                id="eng-bob",
                name="bob",
                group="torque",
                slug="bob",
                cell_type="agent",
                kind="engineer",
                persistent=True,
                hired_by_architect_id="arch-1",
            )
        )
        self.db.save_decision(
            {"id": "decision-1", "architect_id": "arch-1", "title": "One", "rationale": "A"}
        )
        self.db.save_decision(
            {"id": "decision-2", "architect_id": "arch-1", "title": "Two", "rationale": "B"}
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["architects"]["total"], 1)
        self.assertEqual(
            report["architects"]["architects"],
            [
                {
                    "id": "arch-1",
                    "name": "productmind",
                    "slug": "productmind",
                    "decision_count": 2,
                    "hired_engineer_count": 1,
                }
            ],
        )
        self.assertIn(
            "- productmind id=arch-1 decisions=2 hired_engineers=1",
            rendered,
        )

    def test_build_doctor_report_warns_for_stale_pending_hires(self):
        home = self._home_dir()
        self._save_engineer()
        self._save_architect()
        now_ts = int(time.time())
        self.db.save_pending_hire(
            {
                "id": "hire-1",
                "architect_id": "arch-1",
                "requested_name": "bob",
                "status": "pending",
                "created_at": now_ts - (26 * 3600),
            }
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertIn(
            {
                "name": "stale_pending_hire",
                "status": "warn",
                "details": {
                    "id": "hire-1",
                    "architect_id": "arch-1",
                    "architect_name": "productmind",
                    "age_hours": 26,
                },
            },
            report["warnings"],
        )
        self.assertEqual(report["pending_hires"]["pending"], 1)
        self.assertEqual(report["pending_hires"]["stale_pending"], 1)
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "pending hire hire-1 from productmind has been waiting 26 hours; approve or reject",
            rendered,
        )

    def test_build_doctor_report_warns_for_dangling_decision_architect(self):
        home = self._home_dir()
        self._save_engineer()
        self.db.save_decision(
            {
                "id": "decision-dangling",
                "architect_id": "arch-missing",
                "title": "Dangling",
                "rationale": "Missing architect",
            }
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertIn(
            {
                "name": "dangling_decision_architect",
                "status": "warn",
                "details": {
                    "id": "decision-dangling",
                    "architect_id": "arch-missing",
                },
            },
            report["warnings"],
        )
        self.assertIn(
            "decision decision-dangling points at missing architect arch-missing",
            rendered,
        )

    def test_build_doctor_report_fails_for_architect_hired_by_architect_corruption(self):
        home = self._home_dir()
        self._save_engineer()
        self._save_architect(hired_by_architect_id="arch-parent")

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "fail")
        self.assertIn("invalid_architect_hired_by_architect", report["failed_checks"])
        self.assertIn(
            "architect productmind has hired_by_architect_id, invalid state",
            rendered,
        )

    def test_build_doctor_report_counts_roles_preamble_and_priorities(self):
        home = self._home_dir()
        (home / ".torque" / "roles").mkdir(parents=True, exist_ok=True)
        (home / ".torque" / "roles" / "careful.yaml").write_text(
            "name: careful\npreamble: |\n  Be careful.\n",
            encoding="utf-8",
        )
        (home / ".torque" / "roles" / "reviewer.yaml").write_text(
            "name: reviewer\npriorities:\n  - ship small\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            report["warnings"],
            [
                {
                    "name": "no_engineers",
                    "status": "warn",
                    "details": {
                        "count": 0,
                        "hint": (
                            "no engineer exists; create one from the Agent panel "
                            "before using engineer MCP tools"
                        ),
                    },
                }
            ],
        )
        self.assertEqual(report["roles"]["roles_file_count"], 2)
        self.assertEqual(report["roles"]["roles_with_preamble"], 1)
        self.assertEqual(report["roles"]["roles_with_priorities"], 1)
        self.assertIn("roles_dir:                      ~/.torque/roles (2 files)", rendered)
        self.assertIn("roles_with_preamble:            1", rendered)
        self.assertIn("roles_with_priorities:          1", rendered)

    def test_build_doctor_report_warns_when_engineer_exists_with_unassigned_tasks(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="engineer-1",
                name="Engineer",
                group="torque",
                slug="engineer",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Unassigned task",
                slug="unassigned-task",
                group="g",
            )
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            [warning["name"] for warning in report["warnings"]],
            [
                "unassigned_tasks_when_engineer_present",
                "engineer_generalist_specialization",
            ],
        )
        self.assertEqual(report["tasks"]["unassigned_when_engineer_present"], 1)
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn("engineer present but unassigned tasks remain: 1", rendered)

    def test_build_doctor_report_advises_about_default_generalist_engineers(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="eng-alice",
                name="Alice",
                group="torque",
                slug="alice",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_agent(
            AgentCell(
                id="eng-bob",
                name="Bob",
                group="torque",
                slug="bob",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["engineers"]["total"], 2)
        self.assertEqual(
            [warning["name"] for warning in report["warnings"]],
            ["engineer_generalist_specialization"],
        )
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertNotIn("default (engineer_* routing)", rendered)

    def test_build_doctor_report_passes_for_multiple_engineers_when_engineer_exists(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="eng-engineer",
                name="Engineer",
                group="torque",
                slug="engineer",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_agent(
            AgentCell(
                id="eng-alice",
                name="Alice",
                group="torque",
                slug="alice",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(
            [warning["name"] for warning in report["warnings"]],
            ["engineer_generalist_specialization"],
        )
        self.assertEqual(report["engineers"]["total"], 2)
        self.assertNotIn("default (engineer_* routing)", rendered)

    def test_build_doctor_report_flags_drift_and_unmigrated_rows(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="worker-1",
                name="Worker",
                group="g",
                slug="worker",
                cell_type="agent",
                template="researcher",
                role="researcher",
                created_by_engineer_id="engineer-1",
                owner_engineer_id="engineer-1",
                kind="worker",
            )
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Owned task",
                slug="owned-task",
                group="g",
                assigned_engineer_id="engineer-1",
            )
        )
        self._add_legacy_kinds_columns()
        self.db._conn.execute(
            "UPDATE agents SET template='researcher', created_by_engineer_id='engineer-1', "
            "kind='', role='', owner_engineer_id='other-engineer' "
            "WHERE id='worker-1'"
        )
        self.db._conn.execute(
            "UPDATE board_tasks SET engineer_owner_id='engineer-1', "
            "assigned_engineer_id='other-engineer' WHERE id='task-1'"
        )
        self.db._conn.commit()

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "fail")
        self.assertCountEqual(
            report["failed_checks"],
            [
                "unmigrated_agents",
                "agents_template_role_drift",
                "agents_created_by_owner_drift",
                "board_tasks_owner_drift",
                "stage_6_legacy_columns_removed",
            ],
        )
        self.assertEqual(report["agents"]["unmigrated"], 1)
        self.assertEqual(report["drift"]["agents_template_role"], 1)
        self.assertEqual(
            report["drift"]["agents_created_by_engineer_owner_engineer"],
            1,
        )
        self.assertEqual(
            report["drift"]["board_tasks_engineer_owner_assigned_engineer"],
            1,
        )
        self.assertTrue(report["drift"]["board_tasks_legacy_column_present"])
        self.assertIn("Result: FAIL", rendered)
        self.assertIn("unmigrated agent rows: 1", rendered)
        self.assertIn("agents.template ↔ role drift: 1", rendered)
        self.assertIn(
            "board_tasks.engineer_owner_id ↔ assigned_engineer_id drift: 1",
            rendered,
        )
        self.assertIn(
            "legacy kinds-refactor columns are still present; complete the stage-6 cleanup migration",
            rendered,
        )

    def test_build_doctor_report_warns_for_ignored_legacy_templates(self):
        home = self._home_dir()
        (home / ".torque" / "agents" / "shared.yaml").write_text(
            "name: shared\ndescription: legacy\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["failed_checks"], [])
        self.assertEqual(report["stage_6_cleanup"]["legacy_template_files_ignored"], 1)
        self.assertEqual(
            report["warnings"],
            [
                {
                    "name": "legacy_template_files_ignored",
                    "status": "warn",
                    "details": {
                        "count": 1,
                        "files": ["~/.torque/agents/shared.yaml"],
                        "hint": (
                            "legacy template files in agents/ are ignored; "
                            "move them into roles/"
                        ),
                    },
                },
                {
                    "name": "no_engineers",
                    "status": "warn",
                    "details": {
                        "count": 0,
                        "hint": (
                            "no engineer exists; create one from the Agent panel "
                            "before using engineer MCP tools"
                        ),
                    },
                }
            ],
        )
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "legacy template files in agents/ are ignored; move them into roles/: ~/.torque/agents/shared.yaml",
            rendered,
        )
        self.assertIn(
            "no engineer exists; create one from the Agent panel before using engineer MCP tools",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
