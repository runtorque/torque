import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.agent_classes import (
    EXTERNAL_CONNECTOR_DRAFT_WARNING,
    agent_class_context_for_cell,
    agent_class_prompt_block_for_cell,
    enriched_agent_class_preview,
    load_agent_classes,
    validate_class_data,
)
from torque.agent_profiles import (
    BASE_KIND_CEILINGS,
    mcp_tool_allowed_by_policy,
    profile_policy_from_definition,
)
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState


class AgentClassRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)

    def _project_class_dir(self) -> Path:
        path = self.root / "repo" / ".torque" / "agent_classes"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_project_class(self, name: str, text: str) -> Path:
        path = self._project_class_dir() / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_builtin_classes_reference_expected_profiles(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))

        self.assertEqual([], [issue.as_dict() for issue in issues])
        by_id = {definition.id: definition for definition in classes}
        expected = {
            "default-architect": ("architect", "full-architect", "1"),
            "default-engineer": ("engineer", "full-engineer", "1"),
            "default-worker": ("worker", "full-worker", "1"),
            "product-manager": ("architect", "product-manager-draft", "2"),
        }
        self.assertEqual(set(expected), set(by_id))
        for class_id, (base_kind, profile_id, profile_version) in expected.items():
            definition = by_id[class_id]
            self.assertTrue(definition.builtin)
            self.assertEqual(definition.base_kind, base_kind)
            self.assertEqual(definition.agent_profile_ref.id, profile_id)
            self.assertEqual(definition.agent_profile_ref.version, profile_version)

    def test_product_manager_preview_is_draft_pm_profile_pairing_with_caveat(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        pm = next(definition for definition in classes if definition.id == "product-manager")

        preview = enriched_agent_class_preview(pm, base_dir=str(self.project))

        self.assertEqual(preview["status"], "draft")
        self.assertEqual(preview["agent_profile_ref"], {"id": "product-manager-draft", "version": "2"})
        self.assertEqual(preview["agent_profile"]["id"], "product-manager-draft")
        self.assertTrue(preview["draft"]["scratch_only"])
        self.assertIn("external_connector_caveat", preview)
        warnings = "\n".join(preview["warnings"])
        self.assertIn("scratch-only", warnings)
        self.assertIn("architect_product_*", warnings)
        self.assertIn(EXTERNAL_CONNECTOR_DRAFT_WARNING, preview["warnings"])
        categories = {
            entry["category"]: entry
            for entry in preview["agent_profile"]["projected_tool_categories"]
        }
        self.assertEqual(categories["pm_decisions"]["status"], "allowed")
        self.assertEqual(categories["worker_dispatch"]["status"], "denied")

    def test_invalid_config_rejects_raw_tools_profile_confusion_and_bad_draft(self):
        _definition, issues = validate_class_data(
            {
                "id": "bad-class",
                "version": "1",
                "base_kind": "architect",
                "profile": "Default",
                "agent_profile_ref": {"id": "full-worker", "version": "1"},
                "tools": ["architect_task_create"],
                "grants": ["task.dispatch"],
                "lifecycle": "draft",
                "draft": {"scratch_only": False, "approved_for_live_dogfood": True},
            },
            base_dir=str(self.project),
        )
        codes = [issue.code for issue in issues]

        self.assertIn("agent_cell_profile_confusion", codes)
        self.assertIn("raw_tool_fields_forbidden", codes)
        self.assertIn("invalid_draft_metadata", codes)
        self.assertIn("agent_profile_base_kind_mismatch", codes)

    def test_project_config_path_duplicate_and_docs(self):
        self._write_project_class(
            "default-worker.yaml",
            "\n".join([
                "id: default-worker",
                'version: "1"',
                "base_kind: worker",
                "agent_profile_ref:",
                "  id: full-worker",
                '  version: "1"',
                "",
            ]),
        )

        _classes, issues = load_agent_classes(base_dir=str(self.project))
        docs = Path("docs/reference/agent-classes.md").read_text(encoding="utf-8")

        self.assertIn("duplicate_class_id", [issue.code for issue in issues])
        self.assertIn(".torque/agent_classes/*.yaml", docs)
        self.assertIn("Agent Profile remains the enforcement layer", docs)


class AgentClassStorageLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo"
        self.project.mkdir()
        self.db = TorqueDB(self.root / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.groups["g"] = []

    def _add_agent(self, *, kind="architect", agent_id="agent-1"):
        cell = AgentCell(
            id=agent_id,
            name=kind.title(),
            group="g",
            cell_type="agent",
            kind=kind,
        )
        self.state.agents[cell.id] = cell
        self.state.groups["g"].append(cell.id)
        self.state._db_save_agent(cell)
        return cell

    def test_assigning_class_persists_desired_state_without_mutating_running_effective(self):
        cell = self._add_agent(kind="architect")
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        status = self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            actor_label="test-user",
            base_dir=str(self.project),
        )

        self.assertEqual(status["assigned_class_id"], "product-manager")
        self.assertEqual(status["effective_class_id"], "default-architect")
        self.assertEqual(cell.effective_agent_class_id, "default-architect")
        self.assertEqual(cell.effective_agent_profile_id, "full-architect")
        self.assertTrue(status["pending_next_launch"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["agent_class_id"], "product-manager")
        audit = self.db.list_agent_class_audit(agent_id=cell.id)
        self.assertEqual(audit[0]["event"], "assignment_set")
        self.assertEqual(audit[0]["assigned_profile_id"], "product-manager-draft")

    def test_launch_freezes_class_and_referenced_profile_snapshot(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )

        snapshot = self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=str(self.project),
        )

        self.assertEqual(snapshot["id"], "product-manager")
        self.assertEqual(snapshot["agent_profile_ref"], {"id": "product-manager-draft", "version": "2"})
        self.assertEqual(snapshot["agent_profile"]["id"], "product-manager-draft")
        self.assertEqual(cell.effective_agent_class_id, "product-manager")
        self.assertEqual(cell.effective_agent_profile_id, "product-manager-draft")
        self.assertEqual(cell.effective_agent_profile_version, "2")
        self.assertTrue(snapshot["snapshot_hash"])
        self.assertFalse(
            self.state.agent_class_status_for_cell(cell, base_dir=str(self.project))["pending_next_launch"]
        )
        self.assertFalse(
            self.state.agent_profile_status_for_cell(cell, base_dir=str(self.project))["pending_next_launch"]
        )
        audit = self.db.list_agent_class_audit(agent_id=cell.id)
        self.assertEqual(audit[0]["event"], "effective_snapshot_applied")
        self.assertTrue(audit[0]["snapshot_hash"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["effective_agent_class_snapshot"]["id"], "product-manager")
        self.assertEqual(loaded["effective_agent_profile_snapshot"]["id"], "product-manager-draft")

    def test_class_driven_profile_projection_denies_raw_pm_dangerous_tools(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        policy = profile_policy_from_definition(cell.effective_agent_profile_snapshot)

        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_board_summary", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", policy))

    def test_default_unassigned_classes_preserve_full_base_kind_profiles_and_no_prompt(self):
        for kind, expected_class, expected_profile in [
            ("architect", "default-architect", "full-architect"),
            ("engineer", "default-engineer", "full-engineer"),
            ("worker", "default-worker", "full-worker"),
        ]:
            cell = self._add_agent(kind=kind, agent_id=f"{kind}-1")

            self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))
            policy = profile_policy_from_definition(cell.effective_agent_profile_snapshot)

            self.assertEqual(cell.effective_agent_class_id, expected_class)
            self.assertEqual(cell.effective_agent_profile_id, expected_profile)
            self.assertEqual(set(policy.grants), BASE_KIND_CEILINGS[kind])
            self.assertTrue(policy.is_full_base_kind_profile)
            self.assertEqual(agent_class_prompt_block_for_cell(cell), "")

    def test_class_prompt_block_and_template_context_are_compact(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        prompt_block = agent_class_prompt_block_for_cell(cell)
        context = agent_class_context_for_cell(cell)

        self.assertIn("## Agent Class", prompt_block)
        self.assertIn("Product Manager", prompt_block)
        self.assertIn("Referenced Agent Profile: product-manager-draft@2", prompt_block)
        self.assertEqual(context["id"], "product-manager")
        self.assertEqual(context["agent_profile_id"], "product-manager-draft")
        self.assertLessEqual(len(context["warnings"]), 6)

    def test_cross_kind_class_assignment_and_non_user_assignment_are_rejected(self):
        worker = self._add_agent(kind="worker")

        with self.assertRaises(ValueError):
            self.state.assign_agent_class(worker.id, "product-manager", base_dir=str(self.project))
        with self.assertRaises(PermissionError):
            self.state.assign_agent_class(
                worker.id,
                "default-worker",
                actor_kind="architect",
                base_dir=str(self.project),
            )


class AgentClassDoctorAndCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo"
        self.project.mkdir()
        self.db = TorqueDB(self.root / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.groups["g"] = []
        self.cell = AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
            directory=str(self.project),
        )
        self.state.agents[self.cell.id] = self.cell
        self.state.groups["g"].append(self.cell.id)
        self.state._db_save_agent(self.cell)

    def test_doctor_reports_invalid_class_and_agent_classes_section(self):
        class_dir = self.project / ".torque" / "agent_classes"
        class_dir.mkdir(parents=True)
        (class_dir / "bad.yaml").write_text(
            "\n".join([
                "id: bad-class",
                'version: "1"',
                "base_kind: architect",
                "profile: Default",
                "tools:",
                "  - architect_task_create",
                "agent_profile_ref:",
                "  id: full-worker",
                '  version: "1"',
                "",
            ]),
            encoding="utf-8",
        )
        from torque.doctor import build_doctor_report_for_db, format_doctor_report

        report = build_doctor_report_for_db(
            self.db.db_path,
            project_base_dir=str(self.project),
        )
        rendered = format_doctor_report(report)

        self.assertIn("agent_classes_valid", report["failed_checks"])
        self.assertGreaterEqual(report["agent_classes"]["error_count"], 3)
        self.assertIn("[agent_classes]", rendered)
        self.assertIn("Agent Class validation failed[agent_cell_profile_confusion]", rendered)
        self.assertIn("External connector", rendered)

    def test_trusted_server_agent_class_commands(self):
        from torque.server import _handle_agent_class_command

        async def resolve_base_dir(_group):
            return str(self.project)

        async def run_commands():
            listed = await _handle_agent_class_command(
                {"cmd": "agent_class_list", "base_dir": str(self.project)},
                self.state,
                self.db,
                resolve_base_dir,
            )
            preview = await _handle_agent_class_command(
                {"cmd": "agent_class_preview", "class_id": "product-manager", "base_dir": str(self.project)},
                self.state,
                self.db,
                resolve_base_dir,
            )
            assigned = await _handle_agent_class_command(
                {"cmd": "agent_class_assign", "agent_id": self.cell.id, "class_id": "product-manager"},
                self.state,
                self.db,
                resolve_base_dir,
            )
            status = await _handle_agent_class_command(
                {"cmd": "agent_class_status", "agent_id": self.cell.id},
                self.state,
                self.db,
                resolve_base_dir,
            )
            audit = await _handle_agent_class_command(
                {"cmd": "agent_class_audit", "agent_id": self.cell.id},
                self.state,
                self.db,
                resolve_base_dir,
            )
            cleared = await _handle_agent_class_command(
                {"cmd": "agent_class_clear", "agent_id": self.cell.id},
                self.state,
                self.db,
                resolve_base_dir,
            )
            return listed, preview, assigned, status, audit, cleared

        listed, preview, assigned, status, audit, cleared = asyncio.run(run_commands())

        self.assertEqual(listed["type"], "agent_classes")
        self.assertIn("product-manager", {item["id"] for item in listed["classes"]})
        self.assertEqual(preview["agent_class"]["agent_profile_ref"]["id"], "product-manager-draft")
        self.assertEqual(assigned["status"]["assigned_class_id"], "product-manager")
        self.assertTrue(status["status"]["pending_next_launch"])
        self.assertGreaterEqual(len(audit["events"]), 1)
        self.assertEqual(cleared["status"]["assigned_class_id"], "")


if __name__ == "__main__":
    unittest.main()
