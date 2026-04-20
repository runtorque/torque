import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from loom.db import LoomDB
from loom.doctor import build_doctor_report_for_db, format_doctor_report

install_aiohttp_stub()
from loom.state import AgentCell, BoardTask, GroupSettings


class LoomDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "loom.db"
        self.db = LoomDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

    def _home_dir(self) -> Path:
        home = Path(self.tmp.name) / "home"
        (home / ".loom" / "agents").mkdir(parents=True, exist_ok=True)
        return home

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
                            "no engineer exists; weaver_* tool aliases will fail until one is created"
                        ),
                    },
                }
            ],
        )
        self.assertEqual(report["tasks"]["unassigned"], 1)
        self.assertEqual(report["tasks"]["unassigned_when_engineer_present"], 0)
        self.assertEqual(report["engineers"]["total"], 0)
        self.assertEqual(report["engineers"]["default_engineer_id"], "")
        self.assertEqual(report["roles"]["roles_file_count"], 0)
        self.assertEqual(report["roles"]["legacy_templates_file_count"], 0)
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "no engineer exists; weaver_* tool aliases will fail until one is created",
            rendered,
        )
        self.assertIn("roles_dir:                      ~/.loom/roles (0 files)", rendered)

    def test_build_doctor_report_passes_for_fully_assigned_engineer_db(self):
        home = self._home_dir()
        (home / ".loom" / "agents" / "researcher.yaml").write_text(
            "name: researcher\n",
            encoding="utf-8",
        )
        self.db_path.with_name("loom.db.pre-kinds.bak").write_bytes(b"backup")

        self.db.save_agent(
            AgentCell(
                id="weaver-1",
                name="Weaver",
                group="loom",
                slug="weaver",
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
                created_by_weaver_id="weaver-1",
                owner_engineer_id="weaver-1",
                kind="worker",
            )
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Assigned task",
                slug="assigned-task",
                group="g",
                assigned_engineer_id="weaver-1",
            )
        )
        self.db.save_group_settings(
            "g",
            GroupSettings(weaver_agent_id="weaver-1"),
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["failed_checks"], [])
        self.assertGreaterEqual(
            report["migration"]["schema_kinds_migration_version"],
            2,
        )
        self.assertTrue(report["migration"]["backup_exists"])
        self.assertEqual(report["agents"]["total"], 2)
        self.assertEqual(report["agents"]["engineer"], 1)
        self.assertEqual(report["agents"]["engineer_name"], "Weaver")
        self.assertEqual(report["engineers"]["total"], 1)
        self.assertEqual(report["engineers"]["default_engineer_name"], "Weaver")
        self.assertEqual(report["engineers"]["default_engineer_id"], "weaver-1")
        self.assertEqual(
            report["engineers"]["engineers"],
            [
                {
                    "id": "weaver-1",
                    "name": "Weaver",
                    "slug": "weaver",
                    "persistent": 1,
                    "worker_count": 1,
                    "task_count": 1,
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
            report["drift"]["agents_created_by_weaver_owner_engineer"],
            0,
        )
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["roles"]["roles_file_count"], 0)
        self.assertEqual(report["roles"]["legacy_templates_file_count"], 1)
        self.assertIn("Loom doctor — kinds refactor", rendered)
        self.assertIn("Result: PASS", rendered)
        self.assertIn("[engineers]", rendered)
        self.assertIn("default (weaver_* routing):   Weaver (id=weaver-1)", rendered)
        self.assertIn(
            "legacy_templates_dir:           ~/.loom/agents (1 files)",
            rendered,
        )

    def test_build_doctor_report_counts_roles_preamble_and_priorities(self):
        home = self._home_dir()
        (home / ".loom" / "roles").mkdir(parents=True, exist_ok=True)
        (home / ".loom" / "roles" / "careful.yaml").write_text(
            "name: careful\npreamble: |\n  Be careful.\n",
            encoding="utf-8",
        )
        (home / ".loom" / "roles" / "reviewer.yaml").write_text(
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
                            "no engineer exists; weaver_* tool aliases will fail until one is created"
                        ),
                    },
                }
            ],
        )
        self.assertEqual(report["roles"]["roles_file_count"], 2)
        self.assertEqual(report["roles"]["roles_with_preamble"], 1)
        self.assertEqual(report["roles"]["roles_with_priorities"], 1)
        self.assertIn("roles_dir:                      ~/.loom/roles (2 files)", rendered)
        self.assertIn("roles_with_preamble:            1", rendered)
        self.assertIn("roles_with_priorities:          1", rendered)

    def test_build_doctor_report_warns_when_engineer_exists_with_unassigned_tasks(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="weaver-1",
                name="Weaver",
                group="loom",
                slug="weaver",
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
            report["warnings"],
            [
                {
                    "name": "unassigned_tasks_when_engineer_present",
                    "status": "warn",
                    "details": {"count": 1, "engineer_count": 1},
                }
            ],
        )
        self.assertEqual(report["tasks"]["unassigned_when_engineer_present"], 1)
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn("engineer present but unassigned tasks remain: 1", rendered)

    def test_build_doctor_report_warns_when_multiple_engineers_lack_weaver_name(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="eng-alice",
                name="Alice",
                group="loom",
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
                group="loom",
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
        self.assertEqual(report["engineers"]["default_engineer_name"], "Alice")
        self.assertIn(
            {
                "name": "ambiguous_default_engineer_routing",
                "status": "warn",
                "details": {
                    "count": 2,
                    "default_engineer_id": "eng-alice",
                    "default_engineer_name": "Alice",
                    "hint": (
                        "multiple engineers but no canonical 'Weaver' for default routing; "
                        "weaver_* aliases will pick the earliest by creation order"
                    ),
                },
            },
            report["warnings"],
        )
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "multiple engineers but no canonical 'Weaver' for default routing; "
            "weaver_* aliases will pick the earliest by creation order",
            rendered,
        )

    def test_build_doctor_report_passes_for_multiple_engineers_when_weaver_exists(self):
        home = self._home_dir()
        self.db.save_agent(
            AgentCell(
                id="eng-weaver",
                name="Weaver",
                group="loom",
                slug="weaver",
                cell_type="agent",
                kind="engineer",
                persistent=True,
            )
        )
        self.db.save_agent(
            AgentCell(
                id="eng-alice",
                name="Alice",
                group="loom",
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
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["engineers"]["total"], 2)
        self.assertEqual(report["engineers"]["default_engineer_id"], "eng-weaver")
        self.assertEqual(report["engineers"]["default_engineer_name"], "Weaver")
        self.assertIn("default (weaver_* routing):   Weaver (id=eng-weaver)", rendered)

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
                created_by_weaver_id="weaver-1",
                owner_engineer_id="weaver-1",
                kind="worker",
            )
        )
        self.db.save_board_task(
            BoardTask(
                id="task-1",
                task="Owned task",
                slug="owned-task",
                group="g",
                assigned_engineer_id="weaver-1",
            )
        )
        self.db._conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN weaver_owner_id TEXT NOT NULL DEFAULT ''"
        )
        self.db._conn.execute(
            "UPDATE agents SET kind='', role='', owner_engineer_id='other-engineer' "
            "WHERE id='worker-1'"
        )
        self.db._conn.execute(
            "UPDATE board_tasks SET weaver_owner_id='weaver-1', "
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
            ],
        )
        self.assertEqual(report["agents"]["unmigrated"], 1)
        self.assertEqual(report["drift"]["agents_template_role"], 1)
        self.assertEqual(
            report["drift"]["agents_created_by_weaver_owner_engineer"],
            1,
        )
        self.assertEqual(
            report["drift"]["board_tasks_weaver_owner_assigned_engineer"],
            1,
        )
        self.assertTrue(report["drift"]["board_tasks_legacy_column_present"])
        self.assertIn("Result: FAIL", rendered)
        self.assertIn("unmigrated agent rows: 1", rendered)
        self.assertIn("agents.template ↔ role drift: 1", rendered)
        self.assertIn(
            "board_tasks.weaver_owner_id ↔ assigned_engineer_id drift: 1",
            rendered,
        )

    def test_build_doctor_report_warns_for_shadowed_legacy_templates(self):
        home = self._home_dir()
        (home / ".loom" / "agents" / "shared.yaml").write_text(
            "name: shared\ndescription: legacy\n",
            encoding="utf-8",
        )
        (home / ".loom" / "roles").mkdir(parents=True, exist_ok=True)
        (home / ".loom" / "roles" / "shared.yaml").write_text(
            "name: shared\npreamble: |\n  New role.\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            report = build_doctor_report_for_db(self.db_path)
            rendered = format_doctor_report(report)

        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["failed_checks"], [])
        self.assertEqual(report["roles"]["shadowed_legacy_templates"], 1)
        self.assertEqual(
            report["warnings"],
            [
                {
                    "name": "shadowed_legacy_templates",
                    "status": "warn",
                    "details": {
                        "count": 1,
                        "slugs": ["shared"],
                        "hint": (
                            "legacy template shadowed by new role; "
                            "consider migrating the legacy file"
                        ),
                    },
                },
                {
                    "name": "no_engineers",
                    "status": "warn",
                    "details": {
                        "count": 0,
                        "hint": (
                            "no engineer exists; weaver_* tool aliases will fail until one is created"
                        ),
                    },
                }
            ],
        )
        self.assertIn("Result: PASS (with warnings)", rendered)
        self.assertIn(
            "legacy template shadowed by new role; consider migrating the legacy file: shared",
            rendered,
        )
        self.assertIn(
            "no engineer exists; weaver_* tool aliases will fail until one is created",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
