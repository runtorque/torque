import os
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.agent_profiles import (
    AgentProfileDefinition,
    BASE_KIND_CEILINGS,
    PM_DANGEROUS_CAPABILITIES,
    agent_profile_cell_status,
    dry_run_profile_preview,
    enriched_profile_preview,
    load_agent_profiles,
    validate_profile_data,
    mcp_tool_allowed_by_policy,
    profile_policy_by_id,
    profile_policy_from_definition,
)


class AgentProfileRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)

    def _project_profile_dir(self) -> Path:
        path = self.root / "repo" / ".torque" / "agent_profiles"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_project_profile(self, name: str, text: str) -> Path:
        path = self._project_profile_dir() / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_builtin_full_profiles_equal_base_kind_ceilings(self):
        profiles, issues = load_agent_profiles(base_dir=str(self.project))

        self.assertEqual([], [issue.as_dict() for issue in issues])
        by_id = {profile.id: profile for profile in profiles}
        for profile_id, base_kind in [
            ("full-architect", "architect"),
            ("full-engineer", "engineer"),
            ("full-worker", "worker"),
        ]:
            self.assertIn(profile_id, by_id)
            profile = by_id[profile_id]
            self.assertTrue(profile.builtin)
            self.assertEqual(profile.base_kind, base_kind)
            self.assertEqual(set(profile.grants), BASE_KIND_CEILINGS[base_kind])

    def test_product_manager_draft_denies_dangerous_execution_capabilities(self):
        profiles, issues = load_agent_profiles(base_dir=str(self.project))
        self.assertFalse(issues)
        pm = next(profile for profile in profiles if profile.id == "product-manager-draft")

        self.assertEqual(pm.base_kind, "architect")
        self.assertEqual(pm.metadata["archetype"], "product_manager")
        self.assertFalse(set(pm.grants) & PM_DANGEROUS_CAPABILITIES)
        preview = dry_run_profile_preview(pm)
        denied = set(preview["denied_high_risk_capabilities"])
        for atom in [
            "agent.hire_engineer",
            "agent.dispatch_worker",
            "task.dispatch",
            "worktree.merge",
            "deploy.apply",
            "admin.settings",
            "profile.assign",
            "profile.edit",
        ]:
            self.assertIn(atom, denied)
        categories = {entry["category"]: entry for entry in preview["projected_tool_categories"]}
        self.assertEqual(categories["pm_decisions"]["status"], "allowed")
        self.assertEqual(categories["pm_queued_tasks"]["status"], "allowed")
        self.assertEqual(categories["behavior_overlay_self"]["status"], "allowed")
        self.assertEqual(categories["worker_dispatch"]["status"], "denied")
        self.assertEqual(preview["runtime_enforcement"], "mcp_projection_when_effective_profile_is_set")

    def test_profile_policy_reconstructs_grants_from_frozen_preview_snapshot(self):
        profiles, issues = load_agent_profiles(base_dir=str(self.project))
        self.assertFalse(issues)
        by_id = {profile.id: profile for profile in profiles}

        full_snapshot = enriched_profile_preview(by_id["full-architect"])
        pm_snapshot = enriched_profile_preview(by_id["product-manager-draft"])

        full_policy = profile_policy_from_definition(full_snapshot)
        pm_policy = profile_policy_from_definition(pm_snapshot)

        self.assertTrue(full_policy.is_full_base_kind_profile)
        self.assertTrue(mcp_tool_allowed_by_policy("architect_engineer_hire", full_policy))
        self.assertTrue(mcp_tool_allowed_by_policy("torque_context", full_policy))
        self.assertFalse(pm_policy.is_full_base_kind_profile)
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_board_summary", pm_policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_peer_message", pm_policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_read", pm_policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose", pm_policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_rollback", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_board_summary", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_message", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_inbox", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_reply", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose_for_engineer", pm_policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose_for_role", pm_policy))

    def test_profile_policy_evaluator_allows_full_and_denies_pm_dangerous_tools(self):
        full_architect = profile_policy_by_id("full-architect", base_dir=str(self.project))
        pm = profile_policy_by_id("product-manager-draft", base_dir=str(self.project))

        self.assertIsNotNone(full_architect)
        self.assertIsNotNone(pm)
        self.assertTrue(full_architect.is_full_base_kind_profile)
        self.assertTrue(mcp_tool_allowed_by_policy("architect_engineer_hire", full_architect))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_get_architect_settings", full_architect))

        self.assertFalse(pm.is_full_base_kind_profile)
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_board_summary", pm))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_peer_message", pm))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_read", pm))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose", pm))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_behavior_overlay_rollback", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_board_summary", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_message", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_inbox", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_reply", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_get_architect_settings", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_mcp_calls", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose_for_engineer", pm))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose_for_role", pm))


    def test_torque_steward_policy_denies_raw_tool_picker_only_allows_read_observation(self):
        steward = profile_policy_from_definition(AgentProfileDefinition(
            id="class-policy-torque-steward",
            version="1",
            base_kind="architect",
            display_name="Torque Steward internal policy",
            grants=[
                "observe.self_context",
                "observe.board_summary",
                "observe.task_detail",
                "observe.events",
                "observe.mcp_calls",
                "planning.area_read",
                "planning.initiative_read",
                "decision.list",
                "task.board_sync_read",
            ],
            metadata={
                "archetype": "torque_steward",
                "generated_by_agent_class": {"id": "torque-steward"},
            },
        ))
        pm = profile_policy_by_id("product-manager-draft", base_dir=str(self.project))
        self.assertIsNotNone(pm)

        self.assertTrue(mcp_tool_allowed_by_policy("architect_board_summary", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_steward_operating_brief", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_events_recent", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_task_show", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_area_list", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_initiative_list", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_decision_list", steward))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_mcp_calls", steward))

        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("engineer_tool_search", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_message_user", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_message", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_message", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_update", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_move", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_get_architect_settings", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_read", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_propose", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_create", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_update", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("engineer_merge", steward))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_steward_operating_brief", pm))


    def test_enriched_preview_warns_for_product_manager_draft(self):
        profiles, issues = load_agent_profiles(base_dir=str(self.project))
        self.assertFalse(issues)
        pm = next(profile for profile in profiles if profile.id == "product-manager-draft")

        preview = enriched_profile_preview(pm)

        self.assertEqual(preview["status"], "draft")
        self.assertTrue(any("scratch-only" in warning for warning in preview["warnings"]))
        self.assertTrue(any("architect_product_*" in warning for warning in preview["warnings"]))

    def test_cell_status_defaults_to_full_base_kind_without_assignment(self):
        class Cell:
            id = "worker-1"
            name = "Worker"
            kind = "worker"
            agent_profile_id = ""
            agent_profile_version = ""
            agent_profile_assigned_at = 0
            agent_profile_assigned_by = ""
            effective_agent_profile_id = ""
            effective_agent_profile_version = ""
            effective_agent_profile_snapshot = {}
            effective_agent_profile_applied_at = 0

        status = agent_profile_cell_status(Cell(), base_dir=str(self.project))

        self.assertEqual(status["effective_profile_id"], "full-worker")
        self.assertEqual(status["status"], "full")
        self.assertFalse(status["pending_next_launch"])

    def test_cell_status_requires_effective_version_to_match_desired_assignment(self):
        class Cell:
            id = "arch-1"
            name = "Architect"
            kind = "architect"
            agent_profile_id = "product-manager-draft"
            agent_profile_version = "2"
            agent_profile_assigned_at = 1
            agent_profile_assigned_by = "test"
            effective_agent_profile_id = "product-manager-draft"
            effective_agent_profile_version = ""
            effective_agent_profile_snapshot = {
                "id": "product-manager-draft",
                "base_kind": "architect",
                "status": "draft",
            }
            effective_agent_profile_applied_at = 2

        status = agent_profile_cell_status(Cell(), base_dir=str(self.project))

        self.assertEqual(status["effective_profile_id"], "product-manager-draft")
        self.assertEqual(status["effective_profile_version"], "")
        self.assertEqual(status["next_launch_profile_version"], "2")
        self.assertTrue(status["pending_next_launch"])

    def test_unknown_capability_atom_fails_validation(self):
        _profile, issues = validate_profile_data({
            "id": "bad",
            "version": "1",
            "base_kind": "worker",
            "grants": ["observe.self_context", "bogus.atom"],
        })

        self.assertIn("unknown_capability_atoms", [issue.code for issue in issues])

    def test_grant_outside_base_kind_ceiling_fails_validation(self):
        _profile, issues = validate_profile_data({
            "id": "bad-worker",
            "version": "1",
            "base_kind": "worker",
            "grants": ["agent.hire_engineer"],
        })

        self.assertIn("grants_outside_base_kind_ceiling", [issue.code for issue in issues])

    def test_product_manager_dangerous_grant_fails_validation(self):
        _profile, issues = validate_profile_data({
            "id": "product-manager-custom",
            "version": "1",
            "base_kind": "architect",
            "grants": ["observe.self_context", "task.dispatch"],
            "metadata": {"archetype": "product_manager"},
        })

        self.assertIn("dangerous_product_manager_grants", [issue.code for issue in issues])

    def test_base_kind_mismatch_and_agent_cell_profile_confusion_fail_validation(self):
        _profile, issues = validate_profile_data({
            "id": "full-worker",
            "version": "1",
            "base_kind": "architect",
            "profile": "Default",
            "policy": {"base_kind": "worker"},
            "grants": ["observe.self_context"],
        })
        codes = [issue.code for issue in issues]

        self.assertIn("agent_cell_profile_confusion", codes)
        self.assertIn("profile_base_kind_mismatch", codes)

    def test_project_config_path_loads_profiles_and_docs_name_the_path(self):
        self._write_project_profile(
            "custom.yaml",
            "\n".join([
                "id: custom-worker",
                'version: "1"',
                "base_kind: worker",
                "display_name: Custom Worker",
                "grants:",
                "  - observe.self_context",
                "  - task.complete",
                "policy:",
                "  scope:",
                "    summary: project test profile",
                "",
            ]),
        )

        profiles, issues = load_agent_profiles(base_dir=str(self.project))
        self.assertFalse(issues)
        custom = next(profile for profile in profiles if profile.id == "custom-worker")
        self.assertFalse(custom.builtin)
        self.assertEqual(custom.source, str(self._project_profile_dir() / "custom.yaml"))
        docs = Path("docs/reference/agent-profiles.md").read_text(encoding="utf-8")
        self.assertIn(".torque/agent_profiles/*.yaml", docs)
        self.assertIn("explicit effective", docs)

    def test_malformed_project_profile_reports_load_issue(self):
        path = self._write_project_profile("broken.yaml", "id: [unterminated\n")

        _profiles, issues = load_agent_profiles(base_dir=str(self.project))

        matches = [issue for issue in issues if issue.path == str(path)]
        self.assertEqual(["malformed_yaml"], [issue.code for issue in matches])


class AgentProfileDoctorTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        from torque.db import TorqueDB

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo"
        self.project.mkdir()
        self.db_path = self.root / "torque.db"
        self.db = TorqueDB(self.db_path)
        self.db.init()
        self.addCleanup(self.db.close)

    def test_doctor_fails_invalid_project_profile_and_renders_issue(self):
        profile_dir = self.project / ".torque" / "agent_profiles"
        profile_dir.mkdir(parents=True)
        (profile_dir / "bad.yaml").write_text(
            "\n".join([
                "id: product-manager-bad",
                'version: "1"',
                "base_kind: architect",
                "grants:",
                "  - task.dispatch",
                "profile: Default",
                "metadata:",
                "  archetype: product_manager",
                "",
            ]),
            encoding="utf-8",
        )
        from torque.doctor import build_doctor_report_for_db, format_doctor_report

        report = build_doctor_report_for_db(
            self.db_path,
            project_base_dir=str(self.project),
        )
        rendered = format_doctor_report(report)

        self.assertIn("agent_profiles_valid", report["failed_checks"])
        self.assertEqual(report["agent_profiles"]["error_count"], 2)
        self.assertIn("[agent_profiles]", rendered)
        self.assertIn("agent profile validation failed[dangerous_product_manager_grants]", rendered)
        self.assertIn("agent profile validation failed[agent_cell_profile_confusion]", rendered)

    def test_doctor_reports_assignment_and_audit_surface(self):
        from torque.state import AgentCell, MatrixState
        from torque.doctor import build_doctor_report_for_db, format_doctor_report

        state = MatrixState(db=self.db)
        state.groups["g"] = []
        cell = AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)
        state._db_save_agent(cell)
        state.assign_agent_profile(
            cell.id,
            "product-manager-draft",
            actor_kind="user",
            base_dir=str(self.project),
        )
        state.apply_effective_agent_profile_for_launch(cell, base_dir=str(self.project))

        report = build_doctor_report_for_db(
            self.db_path,
            project_base_dir=str(self.project),
        )
        rendered = format_doctor_report(report)

        self.assertGreaterEqual(report["agent_profiles"]["assignment_count"], 1)
        self.assertGreaterEqual(report["agent_profiles"]["audit_recent_count"], 1)
        self.assertEqual(
            report["agent_profiles"]["assignments"][0]["effective_profile_id"],
            "product-manager-draft",
        )
        self.assertIn("assignment_count", rendered)
        self.assertIn("audit_recent_count", rendered)
