import asyncio
import tempfile
import time
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.agent_classes import (
    agent_class_context_for_cell,
    agent_class_prompt_block_for_cell,
    agent_class_definition_by_id,
    agent_class_authoring_contract,
    archive_custom_agent_class,
    compile_agent_class_profile,
    delete_custom_agent_class,
    enriched_agent_class_preview,
    load_agent_classes,
    save_custom_agent_class,
    validate_class_data,
    validate_agent_class_draft,
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
            "creative-architect": ("architect", "class-policy-creative-architect", "1"),
            "default-architect": ("architect", "full-architect", "1"),
            "default-engineer": ("engineer", "full-engineer", "1"),
            "default-worker": ("worker", "full-worker", "1"),
            "product-manager": ("architect", "class-policy-product-manager", "3"),
            "torque-steward": ("architect", "class-policy-torque-steward", "1"),
        }
        self.assertEqual(set(expected), set(by_id))
        for class_id, (base_kind, profile_id, profile_version) in expected.items():
            definition = by_id[class_id]
            self.assertTrue(definition.builtin)
            self.assertEqual(definition.base_kind, base_kind)
            self.assertEqual(definition.agent_profile_ref.id, profile_id)
            self.assertEqual(definition.agent_profile_ref.version, profile_version)

    def test_product_manager_preview_is_class_first_compiled_pm_policy_with_caveat(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        pm = next(definition for definition in classes if definition.id == "product-manager")

        preview = enriched_agent_class_preview(pm, base_dir=str(self.project))

        self.assertEqual(preview["status"], "restricted")
        self.assertEqual(preview["display_name"], "Product Manager")
        self.assertEqual(preview["primary_identity_label"], "Product Manager")
        self.assertEqual(preview["secondary_base_kind_label"], "Architect-derived")
        self.assertEqual(preview["lifecycle"], "stable")
        self.assertEqual(preview["agent_profile_ref"], {"id": "class-policy-product-manager", "version": "3"})
        self.assertEqual(preview["agent_profile"]["id"], "class-policy-product-manager")
        self.assertEqual(preview["agent_profile"]["status"], "restricted")
        self.assertEqual(preview["internal_policy"]["mode"], "compile")
        self.assertEqual(preview["internal_policy"]["profile_source"], "compiled_from_agent_class")
        self.assertFalse(preview["internal_policy"]["generated_profile_written_to_project_yaml"])
        self.assertEqual(preview["capability_bucket_selection"], [
            "self_context",
            "planning_reads",
            "proposed_decisions",
            "board_task_proposals",
            "behavior_overlay_self",
            "user_messages",
            "product_peer_messages",
            "private_journal",
        ])
        self.assertEqual(preview["restriction_bucket_selection"], [])
        self.assertIn("capability_bucket_catalog", preview["authoring_contract"])
        self.assertEqual(preview["draft"], {})
        self.assertTrue(preview["metadata"]["approved_for_live_dogfood"])
        self.assertEqual(preview["metadata"]["permanence_state"], "dogfood_permanent")
        self.assertEqual(preview["acl"]["mode"], "allow")
        self.assertIn("architect_product_*", preview["acl"]["allowed_families"])
        self.assertEqual(preview["authority_summary"]["mode"], "allow")
        self.assertIn("external_connector_caveat", preview)
        warnings = "\n".join(preview["warnings"])
        self.assertIn("narrows the base kind", warnings)
        categories = {
            entry["category"]: entry
            for entry in preview["agent_profile"]["projected_tool_categories"]
        }
        self.assertEqual(categories["pm_decisions"]["status"], "allowed")
        self.assertEqual(categories["behavior_overlay_self"]["status"], "allowed")
        self.assertEqual(categories["worker_dispatch"]["status"], "denied")
        compiled = compile_agent_class_profile(pm)
        self.assertEqual(compiled.id, "class-policy-product-manager")
        self.assertIn("behavior_overlay.read", compiled.grants)
        self.assertIn("behavior_overlay.propose_self", compiled.grants)
        self.assertEqual(set(compiled.grants) & {
            "agent.hire_engineer",
            "agent.dispatch_worker",
            "task.dispatch",
            "worktree.merge",
            "deploy.apply",
            "admin.settings",
            "profile.assign",
            "profile.edit",
        }, set())

    def test_creative_architect_preview_is_proposal_only_thinking_class(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        creative = next(definition for definition in classes if definition.id == "creative-architect")

        preview = enriched_agent_class_preview(creative, base_dir=str(self.project))

        self.assertEqual(preview["status"], "restricted")
        self.assertEqual(preview["display_name"], "Creative")
        self.assertEqual(preview["primary_identity_label"], "Creative")
        self.assertEqual(preview["secondary_base_kind_label"], "Architect-derived")
        self.assertEqual(preview["agent_profile_ref"], {"id": "class-policy-creative-architect", "version": "1"})
        self.assertEqual(preview["agent_profile"]["id"], "class-policy-creative-architect")
        self.assertTrue(preview["metadata"]["proposal_only"])
        self.assertEqual(preview["acl"]["mode"], "allow")
        self.assertIn("architect_product_*", preview["acl"]["allowed_families"])
        self.assertIn("architect_thinking_*", preview["acl"]["allowed_families"])
        for bucket in {
            "self_context",
            "planning_reads",
            "recent_context_reads",
            "thinking_workspace",
            "idea_briefs",
            "proposed_decisions",
            "board_task_proposals",
            "behavior_overlay_self",
            "user_messages",
            "product_peer_messages",
            "private_journal",
        }:
            self.assertIn(bucket, preview["capability_bucket_selection"])
        self.assertEqual(preview["restriction_bucket_selection"], [])
        categories = {
            entry["category"]: entry
            for entry in preview["agent_profile"]["projected_tool_categories"]
        }
        self.assertEqual(categories["thinking_reads"]["status"], "allowed")
        self.assertEqual(categories["thinking_writes"]["status"], "allowed")
        self.assertEqual(categories["idea_briefs"]["status"], "allowed")
        self.assertEqual(categories["behavior_overlay_self"]["status"], "allowed")
        self.assertEqual(categories["worker_dispatch"]["status"], "denied")
        self.assertEqual(categories["deploy_admin"]["status"], "denied")
        warnings = "\n".join(preview["warnings"])
        self.assertIn("proposal-only", warnings)
        self.assertIn("architect_thinking_*", warnings)
        self.assertIn("architect_product_*", warnings)

        compiled = compile_agent_class_profile(creative)
        self.assertEqual(compiled.id, "class-policy-creative-architect")
        self.assertEqual(compiled.display_name, "Creative internal policy")
        self.assertIn("thinking.read", compiled.grants)
        self.assertIn("thinking.write_own", compiled.grants)
        self.assertIn("idea_brief.read", compiled.grants)
        self.assertIn("idea_brief.write_own", compiled.grants)
        self.assertIn("idea_brief.propose", compiled.grants)
        self.assertIn("behavior_overlay.read", compiled.grants)
        self.assertIn("behavior_overlay.propose_self", compiled.grants)
        self.assertEqual(set(compiled.grants) & {
            "agent.hire_engineer",
            "agent.dispatch_worker",
            "agent.engineer_roster_read",
            "task.dispatch",
            "task.create",
            "task.update",
            "task.move",
            "comm.engineer_message",
            "comm.worker_message",
            "worktree.merge",
            "deploy.apply",
            "admin.settings",
            "profile.assign",
            "profile.edit",
            "decision.accept",
            "decision.create",
            "decision.update",
            "planning.area_write",
            "planning.initiative_write",
        }, set())

    def test_torque_steward_preview_is_read_only_observer_foundation(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        steward = next(definition for definition in classes if definition.id == "torque-steward")

        preview = enriched_agent_class_preview(steward, base_dir=str(self.project))

        self.assertEqual(preview["status"], "draft")
        self.assertEqual(preview["display_name"], "Torque Steward")
        self.assertEqual(preview["primary_identity_label"], "Torque Steward")
        self.assertEqual(preview["secondary_base_kind_label"], "Architect-derived")
        self.assertEqual(preview["lifecycle"], "draft")
        self.assertEqual(preview["agent_profile_ref"], {"id": "class-policy-torque-steward", "version": "1"})
        self.assertEqual(preview["agent_profile"]["id"], "class-policy-torque-steward")
        self.assertTrue(preview["metadata"]["represents_user_wishes"])
        self.assertFalse(preview["metadata"]["auto_create_enabled"])
        self.assertEqual(preview["metadata"]["mutating_authority"], "none")
        self.assertEqual(preview["acl"]["mode"], "allow")
        self.assertIn("architect_steward_operating_brief", preview["acl"]["allowed_tools"])
        self.assertIn("architect_message_user", preview["acl"]["allowed_tools"])
        self.assertEqual(preview["acl"]["denied_families"], [])
        self.assertEqual(preview["authority_summary"]["mode"], "allow")
        self.assertEqual(preview["authority_summary"]["high_risk_grants"], [])
        self.assertEqual(preview["capability_bucket_selection"], [
            "self_context",
            "planning_reads",
            "recent_context_reads",
            "board_task_reads",
            "user_messages",
            "peer_architect_messages",
            "private_journal",
        ])
        self.assertEqual(preview["restriction_bucket_selection"], [])
        categories = {
            entry["category"]: entry
            for entry in preview["agent_profile"]["projected_tool_categories"]
        }
        self.assertEqual(categories["planning_reads"]["status"], "allowed")
        self.assertEqual(categories["peer_architect_comm"]["status"], "allowed")
        self.assertEqual(categories["worker_dispatch"]["status"], "denied")
        self.assertEqual(categories["execution_task_control"]["status"], "denied")
        self.assertEqual(categories["deploy_admin"]["status"], "denied")
        self.assertEqual(categories["profile_admin"]["status"], "denied")
        warnings = "\n".join(preview["warnings"])
        self.assertIn("conservative operations steward", warnings)
        self.assertIn("conservative operations steward", warnings)

        compiled = compile_agent_class_profile(steward)
        self.assertEqual(compiled.id, "class-policy-torque-steward")
        self.assertEqual(set(compiled.grants), {
            "observe.self_context",
            "observe.board_summary",
            "observe.task_detail",
            "observe.events",
            "observe.mcp_calls",
            "planning.area_read",
            "planning.initiative_read",
            "decision.list",
            "task.board_sync_read",
            "comm.user_ask",
            "comm.user_message",
            "comm.peer_architect_list",
            "comm.peer_architect_message",
            "journal.private",
        })
        self.assertEqual(set(compiled.grants) & {
            "comm.engineer_message",
            "comm.worker_message",
            "agent.hire_engineer",
            "agent.dispatch_worker",
            "task.dispatch",
            "task.create",
            "task.update",
            "task.move",
            "worktree.merge",
            "deploy.apply",
            "admin.settings",
            "profile.assign",
            "profile.edit",
            "decision.accept",
            "decision.create",
            "decision.update",
            "planning.area_write",
            "planning.initiative_write",
            "memory.publish",
        }, set())

    def test_schema_v4_compile_accepts_acl_and_rejects_raw_tools(self):
        valid = {
            "agent_class_schema_version": 4,
            "id": "planning-architect",
            "version": "1",
            "display_name": "Planning Architect",
            "identity": {"label": "Planning Architect", "primary_ui_label": "Planning Architect"},
            "runtime": {"base_kind": "architect", "base_kind_label": "Architect-derived"},
            "prompt": {"job": "Use planning-safe surfaces only."},
            "acl": {
                "mode": "allow",
                "allow": [
                    {"capability": "self_context"},
                    {"capability": "planning_area_reads"},
                    {"capability": "user_messages"},
                ],
            },
            "warnings": ["External connectors are separate."],
        }

        definition, issues = validate_class_data(valid, base_dir=str(self.project))

        self.assertIsNotNone(definition, [issue.as_dict() for issue in issues])
        self.assertEqual(definition.agent_class_schema_version, 4)
        self.assertEqual(definition.base_kind, "architect")
        self.assertEqual(definition.agent_profile_ref.id, "class-policy-planning-architect")
        compiled = compile_agent_class_profile(definition)
        self.assertEqual(compiled.id, "class-policy-planning-architect")
        self.assertEqual(set(compiled.grants), {
            "observe.self_context",
            "observe.task_detail",
            "planning.area_read",
            "comm.user_ask",
            "comm.user_message",
        })
        self.assertNotIn("agent.dispatch_worker", compiled.grants)
        self.assertEqual(compiled.denies, [])
        preview = enriched_agent_class_preview(definition, base_dir=str(self.project))
        self.assertEqual(preview["capability_bucket_selection"], ["self_context", "planning_area_reads", "user_messages"])
        self.assertIn("Planning Architect internal policy", preview["compiled_profile"]["display_name"])
        self.assertIn("Self and assigned task context", preview["capability_bucket_summary"]["allowed"])
        self.assertFalse(preview["internal_policy"]["generated_profile_written_to_project_yaml"])

        invalid = dict(valid)
        invalid["id"] = "bad-planning"
        invalid["acl"] = {"mode": "allow", "allow": [{"tool": "architect_task_create"}]}
        invalid["tools"] = ["architect_task_create"]
        _definition, invalid_issues = validate_class_data(invalid, base_dir=str(self.project))
        codes = {issue.code for issue in invalid_issues}
        self.assertIn("raw_tool_fields_forbidden", codes)

        mixed = dict(valid)
        mixed["id"] = "mixed-planning"
        mixed["acl"] = {
            "mode": "allow",
            "allow": [{"capability": "self_context"}],
            "deny": [{"action": "worker.dispatch", "scope": "global"}],
        }
        _mixed_definition, mixed_issues = validate_class_data(mixed, base_dir=str(self.project))
        self.assertIn("acl_deny_not_allowed_in_allow_mode", {issue.code for issue in mixed_issues})

        legacy_prompt = dict(valid)
        legacy_prompt["id"] = "legacy-prompt"
        legacy_prompt["prompt"] = {"addendum": "Legacy addendum is not accepted."}
        _legacy_definition, legacy_issues = validate_class_data(legacy_prompt, base_dir=str(self.project))
        self.assertIn("unknown_prompt_fields", {issue.code for issue in legacy_issues})

    def test_bucket_validation_rejects_cross_base_and_pm_broadening(self):
        worker_with_architect_bucket = {
            "agent_class_schema_version": 4,
            "id": "overwide-worker",
            "version": "1",
            "display_name": "Overwide Worker",
            "runtime": {"base_kind": "worker"},
            "acl": {"mode": "allow", "allow": [{"capability": "planning_reads"}]},
        }

        _definition, issues = validate_class_data(worker_with_architect_bucket, base_dir=str(self.project))
        codes = {issue.code for issue in issues}
        self.assertIn("capability_bucket_unavailable_for_base_kind", codes)
        self.assertIn("capability_bucket_outside_base_kind_ceiling", codes)


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

    def test_authoring_validate_save_update_archive_delete_project_yaml(self):
        draft = {
            "id": "custom-architect",
            "version": "1",
            "base_kind": "architect",
            "title": "Custom Architect",
            "description": "Operator-authored class.",
            "agent_profile_ref": {"id": "full-architect", "version": "1"},
            "prompt": {"job": "Use the custom class prompt."},
            "icon": "CA",
            "badge": "custom",
            "color": "#abcdef",
        }

        validation = validate_agent_class_draft(
            draft,
            base_dir=str(self.project),
        )
        self.assertTrue(validation["valid"])
        self.assertEqual(
            validation["normalized"]["metadata"]["ui"]["badge"],
            "custom",
        )
        self.assertTrue(validation["agent_class"]["prompt_summary"]["has_prompt"])

        saved = save_custom_agent_class(
            draft,
            base_dir=str(self.project),
            mode="create",
        )
        self.assertTrue(saved["ok"], saved)
        path = Path(saved["storage"]["path"])
        self.assertEqual(path.name, "custom-architect.yaml")
        self.assertTrue(path.exists())

        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse([issue.as_dict() for issue in issues])
        custom = next(item for item in classes if item.id == "custom-architect")
        preview = enriched_agent_class_preview(custom, base_dir=str(self.project))
        self.assertEqual(preview["source"], "project")
        self.assertTrue(preview["custom"])
        self.assertEqual(preview["status"], "full")
        self.assertEqual(preview["agent_profile_ref"]["id"], "full-architect")

        updated = dict(draft)
        updated["description"] = "Updated description."
        updated["prompt"] = {"job": "Updated prompt."}
        update_result = save_custom_agent_class(
            updated,
            base_dir=str(self.project),
            mode="update",
        )
        self.assertTrue(update_result["ok"], update_result)
        self.assertEqual(update_result["operation"], "updated")
        self.assertIn("Updated prompt", path.read_text(encoding="utf-8"))

        archived = archive_custom_agent_class(
            "custom-architect",
            base_dir=str(self.project),
        )
        self.assertTrue(archived["ok"], archived)
        self.assertEqual(archived["agent_class"]["status"], "archived")
        self.assertIsNone(
            agent_class_definition_by_id(
                "custom-architect",
                base_dir=str(self.project),
            )
        )
        self.assertIsNotNone(
            agent_class_definition_by_id(
                "custom-architect",
                base_dir=str(self.project),
                include_archived=True,
            )
        )

        deleted = delete_custom_agent_class(
            "custom-architect",
            base_dir=str(self.project),
        )
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(path.exists())

    def test_authoring_bucket_compile_contract_hides_generated_profile_authoring(self):
        draft = {
            "id": "pm-lite",
            "version": "1",
            "title": "PM Lite",
            "purpose": "Product planning readout and user updates.",
            "runtime": {"base_kind": "architect", "base_kind_label": "Architect-derived"},
            "capability_buckets": ["self_context", "planning_reads", "user_messages"],
        }

        validation = validate_agent_class_draft(draft, base_dir=str(self.project))

        self.assertTrue(validation["valid"], validation)
        normalized = validation["normalized"]
        self.assertEqual(normalized["description"], "Product planning readout and user updates.")
        self.assertNotIn("policy", normalized)
        self.assertNotIn("agent_profile_ref", normalized)
        self.assertEqual(normalized["capabilities"]["buckets"], ["self_context", "planning_reads", "user_messages"])
        self.assertIn("capability_bucket_catalog", validation["authoring_contract"])
        preview = validation["agent_class"]
        self.assertEqual(preview["agent_profile_ref"], {"id": "class-policy-pm-lite", "version": "1"})
        self.assertEqual(preview["internal_policy"]["profile_source"], "compiled_from_agent_class")
        self.assertEqual(preview["capability_bucket_selection"], ["self_context", "planning_reads", "user_messages"])
        self.assertTrue(preview["apply_state"]["relaunch_required_after_assignment"])
        self.assertEqual(preview["internal_policy"]["snapshot_source"], "sqlite_effective_snapshot_only")

        saved = save_custom_agent_class(draft, base_dir=str(self.project), mode="create")
        self.assertTrue(saved["ok"], saved)
        path = Path(saved["storage"]["path"])
        saved_yaml = path.read_text(encoding="utf-8")
        self.assertIn("buckets:", saved_yaml)
        self.assertNotIn("agent_profile_ref:", saved_yaml)
        self.assertNotIn("generated_profile_id", saved_yaml)

    def test_authoring_rejects_forbidden_fields_before_persistence(self):
        invalid = {
            "id": "bad-custom",
            "version": "1",
            "base_kind": "architect",
            "display_name": "Bad Custom",
            "agent_profile_ref": {"id": "full-worker", "version": "1"},
            "metadata": {"mcp_tools": ["architect_task_create"]},
            "profile_id": "Default",
        }

        result = save_custom_agent_class(
            invalid,
            base_dir=str(self.project),
            mode="create",
        )
        codes = {issue["code"] for issue in result["errors"]}

        self.assertFalse(result["ok"])
        self.assertIn("raw_tool_fields_forbidden", codes)
        self.assertIn("agent_cell_profile_confusion", codes)
        self.assertIn("agent_profile_base_kind_mismatch", codes)
        self.assertFalse((self.root / "repo" / ".torque" / "agent_classes" / "bad-custom.yaml").exists())

    def test_custom_class_save_persists_acl_authoring_shape(self):
        result = save_custom_agent_class({
            "id": "acl-architect",
            "version": "1",
            "agent_class_schema_version": 4,
            "runtime": {"base_kind": "architect"},
            "display_name": "ACL Architect",
            "acl": {
                "mode": "allow",
                "allow": [{"capability": "self_context"}, {"capability": "task_reporting"}, {"action": "message.user", "scope": "self"}],
            },
        }, base_dir=str(self.project), mode="create")

        self.assertTrue(result["ok"], result)
        saved = Path(result["storage"]["path"]).read_text(encoding="utf-8")
        self.assertIn("acl:", saved)
        self.assertIn("mode: allow", saved)
        self.assertIn("capability: self_context", saved)
        self.assertIn("action: message.user", saved)
        self.assertNotIn("policy:", saved)
        definition = agent_class_definition_by_id("acl-architect", base_dir=str(self.project))
        self.assertIsNotNone(definition)
        self.assertEqual(definition.acl.get("mode"), "allow")
        profile = compile_agent_class_profile(definition)
        self.assertIn("comm.user_message", profile.grants)
        self.assertNotIn("agent.dispatch_worker", profile.grants)
        self.assertEqual(profile.denies, [])

    def test_project_config_path_duplicate_and_docs(self):
        self._write_project_class(
            "default-worker.yaml",
            "\n".join([
                "id: default-worker",
                'version: "1"',
                "base_kind: worker",
                "display_name: Default Worker Override",
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
        self.assertIn("Class authority is authored as a generic ACL", docs)


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
        self.assertEqual(audit[0]["assigned_profile_id"], "class-policy-product-manager")

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
        self.assertEqual(snapshot["display_name"], "Product Manager")
        self.assertEqual(snapshot["primary_identity_label"], "Product Manager")
        self.assertEqual(snapshot["agent_profile_ref"], {"id": "class-policy-product-manager", "version": "3"})
        self.assertEqual(snapshot["agent_profile"]["id"], "class-policy-product-manager")
        self.assertEqual(snapshot["internal_policy"]["mode"], "compile")
        self.assertEqual(snapshot["status"], "restricted")
        self.assertEqual(snapshot["acl"]["mode"], "allow")
        self.assertIn("architect_product_*", snapshot["acl"]["allowed_families"])
        self.assertEqual(cell.effective_agent_class_id, "product-manager")
        self.assertEqual(cell.effective_agent_profile_id, "class-policy-product-manager")
        self.assertEqual(cell.effective_agent_profile_version, "3")
        self.assertEqual(
            cell.effective_agent_profile_snapshot["metadata"]["generated_by_agent_class"]["id"],
            "product-manager",
        )
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
        self.assertEqual(loaded["effective_agent_profile_snapshot"]["id"], "class-policy-product-manager")

    def test_product_manager_builtin_version_change_marks_existing_assignment_pending(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=str(self.project),
        )

        # Simulate a live agent assigned/launched before the Product Manager
        # built-in class gained behavior-overlay self grants.  The persisted
        # desired/effective versions can both be old even though the next launch
        # resolves the latest built-in definition by id.
        cell.agent_class_version = "2"
        cell.effective_agent_class_version = "2"
        cell.effective_agent_profile_version = "2"
        cell.effective_agent_class_snapshot["version"] = "2"
        cell.effective_agent_class_snapshot["agent_profile_ref"]["version"] = "2"
        cell.effective_agent_class_snapshot["agent_profile"]["version"] = "2"
        cell.effective_agent_profile_snapshot["version"] = "2"

        class_status = self.state.agent_class_status_for_cell(
            cell,
            base_dir=str(self.project),
        )
        profile_status = self.state.agent_profile_status_for_cell(
            cell,
            base_dir=str(self.project),
        )

        self.assertEqual(class_status["assigned_class_version"], "2")
        self.assertEqual(class_status["effective_class_version"], "2")
        self.assertEqual(class_status["next_launch_class_version"], "3")
        self.assertEqual(class_status["next_launch_profile_version"], "3")
        self.assertTrue(class_status["pending_next_launch"])
        self.assertTrue(class_status["apply_state"]["relaunch_required"])
        self.assertTrue(profile_status["pending_next_launch"])

    def test_direct_profile_launch_after_class_clear_clears_effective_class(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=str(self.project),
        )
        self.assertEqual(cell.effective_agent_class_id, "product-manager")

        self.state.assign_agent_class(
            cell.id,
            "",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.assign_agent_profile(
            cell.id,
            "full-architect",
            actor_kind="user",
            base_dir=str(self.project),
        )
        pre_launch_status = self.state.agent_class_status_for_cell(
            cell,
            base_dir=str(self.project),
        )
        self.assertTrue(pre_launch_status["pending_next_launch"])

        self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=str(self.project),
        )

        self.assertEqual(cell.agent_class_id, "")
        self.assertEqual(cell.agent_profile_id, "full-architect")
        self.assertEqual(cell.effective_agent_profile_id, "full-architect")
        self.assertEqual(cell.effective_agent_class_id, "")
        self.assertEqual(cell.effective_agent_class_version, "")
        self.assertEqual(cell.effective_agent_class_snapshot, {})
        self.assertEqual(agent_class_prompt_block_for_cell(cell), "")
        status = self.state.agent_class_status_for_cell(
            cell,
            base_dir=str(self.project),
        )
        self.assertEqual(status["effective_class_id"], "")
        self.assertEqual(status["next_launch_class_id"], "")
        self.assertEqual(status["next_launch_profile_id"], "full-architect")
        self.assertFalse(status["pending_next_launch"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["effective_agent_class_id"], "")
        self.assertEqual(loaded["effective_agent_class_snapshot"], {})
        audit_events = [
            event["event"]
            for event in self.db.list_agent_class_audit(agent_id=cell.id)
        ]
        self.assertIn("effective_snapshot_cleared", audit_events)

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
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_idea_brief_create", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", policy))

    def test_torque_steward_class_projection_denies_tool_search_and_mutations_but_allows_communication(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "torque-steward",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        policy = profile_policy_from_definition(cell.effective_agent_profile_snapshot)

        self.assertFalse(mcp_tool_allowed_by_policy("architect_board_summary", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_steward_operating_brief", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_events_recent", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_show", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_area_list", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_initiative_list", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_list", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_mcp_calls", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_ask", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_message_user", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_peer_list", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_peer_message", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_journal", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_journal_read", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("engineer_tool_search", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("torque_ask", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("torque_message_user", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_product_message_user", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_product_peer_message", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_digest_filter", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_message", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_update", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_move", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_deploy_state", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_get_architect_settings", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_behavior_overlay_read", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_create", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_update", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("engineer_merge", policy))

    def test_creative_architect_launch_freezes_prompt_and_safe_internal_policy(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "creative-architect",
            actor_kind="user",
            base_dir=str(self.project),
        )

        snapshot = self.state.apply_effective_agent_class_for_launch(
            cell,
            base_dir=str(self.project),
        )
        policy = profile_policy_from_definition(cell.effective_agent_profile_snapshot)
        prompt_block = agent_class_prompt_block_for_cell(cell)

        self.assertEqual(snapshot["id"], "creative-architect")
        self.assertEqual(snapshot["display_name"], "Creative")
        self.assertEqual(snapshot["primary_identity_label"], "Creative")
        self.assertEqual(snapshot["agent_profile_ref"], {"id": "class-policy-creative-architect", "version": "1"})
        self.assertEqual(cell.effective_agent_class_id, "creative-architect")
        self.assertEqual(cell.effective_agent_profile_id, "class-policy-creative-architect")
        self.assertEqual(
            cell.effective_agent_profile_snapshot["metadata"]["generated_by_agent_class"]["id"],
            "creative-architect",
        )
        self.assertIn("imaginative but grounded ideation partner", prompt_block)
        self.assertIn("Diverge first", prompt_block)
        self.assertIn("non-binding until accepted", prompt_block)
        self.assertTrue(mcp_tool_allowed_by_policy("architect_thinking_scratchpad_create", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_thinking_mind_map_node_create", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_idea_brief_create", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_idea_brief_propose", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("architect_product_task_propose", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_board_summary", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_tool_search", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_peer_message", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_message_user", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("torque_message_user", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_decision_link", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_engineer_hire", policy))
        self.assertFalse(mcp_tool_allowed_by_policy("architect_task_create", policy))

        cell.effective_agent_class_snapshot["display_name"] = "Creative Architect"
        cell.effective_agent_class_snapshot["primary_identity_label"] = "Creative Architect"
        status = self.state.agent_class_status_for_cell(cell, base_dir=str(self.project))
        self.assertEqual(status["effective_primary_identity_label"], "Creative")
        self.assertEqual(status["next_launch_primary_identity_label"], "Creative")

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
        self.assertIn("Internal Agent Profile: class-policy-product-manager@3", prompt_block)
        self.assertEqual(context["id"], "product-manager")
        self.assertEqual(context["primary_identity_label"], "Product Manager")
        self.assertEqual(context["agent_profile_id"], "class-policy-product-manager")
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

    def test_doctor_warns_for_legacy_direct_profile_assignment(self):
        self.state.assign_agent_profile(
            self.cell.id,
            "product-manager-draft",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(
            self.cell,
            base_dir=str(self.project),
        )
        from torque.doctor import build_doctor_report_for_db, format_doctor_report

        report = build_doctor_report_for_db(
            self.db.db_path,
            project_base_dir=str(self.project),
        )
        rendered = format_doctor_report(report)

        self.assertEqual(
            report["agent_profiles"].get("legacy_direct_profile_count", 0),
            1,
        )
        self.assertEqual(
            report["agent_classes"].get("legacy_direct_profile_count", 0),
            1,
        )
        self.assertIn("legacy_direct_profile", rendered)
        self.assertIn("no silent migration", rendered)

    def test_trusted_server_agent_class_commands(self):
        from torque.server import _handle_agent_class_command

        async def resolve_base_dir(_group):
            return str(self.project)

        async def run_commands():
            custom_payload = {
                "id": "panel-architect",
                "version": "1",
                "base_kind": "architect",
                "display_name": "Panel Architect",
                "agent_profile_ref": {"id": "full-architect", "version": "1"},
            }
            validation = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_validate",
                    "base_dir": str(self.project),
                    "agent_class": custom_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            saved = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_create",
                    "base_dir": str(self.project),
                    "agent_class": custom_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
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
            archived = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_archive",
                    "base_dir": str(self.project),
                    "class_id": "panel-architect",
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            deleted = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_delete",
                    "base_dir": str(self.project),
                    "class_id": "panel-architect",
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            return validation, saved, listed, preview, assigned, status, audit, cleared, archived, deleted

        validation, saved, listed, preview, assigned, status, audit, cleared, archived, deleted = asyncio.run(run_commands())

        self.assertEqual(validation["type"], "agent_class_validation")
        self.assertTrue(validation["valid"])
        self.assertEqual(saved["type"], "agent_class_save")
        self.assertTrue(saved["ok"])
        self.assertEqual(listed["type"], "agent_classes")
        self.assertIn("capability_bucket_catalog", listed)
        self.assertIn("self_context", {item["id"] for item in listed["capability_bucket_catalog"]})
        self.assertIn("product-manager", {item["id"] for item in listed["classes"]})
        self.assertIn("panel-architect", {item["id"] for item in listed["classes"]})
        self.assertEqual(preview["agent_class"]["primary_identity_label"], "Product Manager")
        self.assertEqual(preview["agent_class"]["agent_profile_ref"]["id"], "class-policy-product-manager")
        self.assertEqual(assigned["status"]["assigned_class_id"], "product-manager")
        self.assertTrue(status["status"]["pending_next_launch"])
        self.assertGreaterEqual(len(audit["events"]), 1)
        self.assertEqual(cleared["status"]["assigned_class_id"], "")
        self.assertEqual(archived["operation"], "archived")
        self.assertTrue(deleted["ok"])

    def test_create_agent_from_class_launch_command_freezes_snapshots(self):
        from torque.server import _handle_agent_class_launch_command

        saved = save_custom_agent_class(
            {
                "id": "launch-architect",
                "version": "1",
                "base_kind": "architect",
                "display_name": "Launch Architect",
                "agent_profile_ref": {"id": "full-architect", "version": "1"},
                "prompt": {"job": "Custom launch prompt."},
            },
            base_dir=str(self.project),
            mode="create",
        )
        self.assertTrue(saved["ok"], saved)

        async def resolve_base_dir(_group):
            return str(self.project)

        def resolve_launch_config(_group, *, base_dir="", explicit_template="", overrides=None):
            del explicit_template, overrides
            return {
                "profile": "Default",
                "command": "codex",
                "directory": base_dir or str(self.project),
                "tab_color": "",
                "icon": "",
                "env_vars": {},
                "env_file": "",
                "shell": "",
                "system_prompt": "",
                "agent_type": "codex",
                "session_resume": True,
                "idle_timeout": 0,
                "worktree": False,
                "worktree_base_dir": ".torque/worktrees",
                "worktree_auto_checkpoint": False,
                "checkpoint_on_progress": False,
                "worktree_merge_squash": True,
                "terminals": [],
            }

        async def create_agent_with_config(group, name, launch_cfg, **kwargs):
            kind = kwargs.get("kind", "")
            cell = AgentCell(
                id=f"{kind}-created",
                name=name,
                group=group,
                cell_type="agent",
                kind=kind,
                directory=launch_cfg.get("directory", ""),
                profile=launch_cfg.get("profile", ""),
                command=launch_cfg.get("command", ""),
            )
            if launch_cfg.get("agent_class_id"):
                cell.agent_class_id = launch_cfg["agent_class_id"]
                cell.agent_class_version = launch_cfg.get("agent_class_version", "")
            self.state.agents[cell.id] = cell
            self.state.groups.setdefault(group, []).append(cell.id)
            self.state.apply_effective_agent_class_for_launch(
                cell,
                base_dir=cell.directory,
            )
            self.state._db_save_agent(cell)
            return cell

        async def send_agent_prompt(*_args, **_kwargs):
            return None

        async def run_launches():
            launched = await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "launch-architect",
                    "name": "Custom Launch",
                    "group": "g",
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )
            mismatch = await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "launch-architect",
                    "kind": "worker",
                    "name": "Wrong Kind",
                    "group": "g",
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )
            return launched, mismatch

        launched, mismatch = asyncio.run(run_launches())

        self.assertEqual(launched["type"], "agent_class_launch")
        self.assertEqual(launched["agent"]["agent_class_status"]["effective_class_id"], "launch-architect")
        cell = self.state.agents["architect-created"]
        self.assertEqual(cell.effective_agent_class_id, "launch-architect")
        self.assertEqual(cell.effective_agent_profile_id, "full-architect")
        self.assertEqual(cell.effective_agent_class_snapshot["prompt"], {"job": "Custom launch prompt."})
        self.assertEqual(mismatch["type"], "error")
        self.assertEqual(mismatch["code"], "agent_class_base_kind_mismatch")

    def test_custom_worker_class_trusted_create_preview_update_launch_and_denials(self):
        from torque.agent_classes import append_agent_class_prompt_block
        from torque.server import _handle_agent_class_command, _handle_agent_class_launch_command

        async def resolve_base_dir(_group):
            return str(self.project)

        def resolve_launch_config(_group, *, base_dir="", explicit_template="", overrides=None):
            del explicit_template, overrides
            return {
                "profile": "Default",
                "command": "codex",
                "directory": base_dir or str(self.project),
                "tab_color": "",
                "icon": "",
                "env_vars": {},
                "env_file": "",
                "shell": "",
                "system_prompt": "",
                "agent_type": "codex",
                "session_resume": True,
                "idle_timeout": 0,
                "worktree": False,
                "worktree_base_dir": ".torque/worktrees",
                "worktree_auto_checkpoint": False,
                "checkpoint_on_progress": False,
                "worktree_merge_squash": True,
                "terminals": [],
            }

        created_prompts = {}

        async def create_agent_with_config(group, name, launch_cfg, **kwargs):
            kind = kwargs.get("kind", "")
            cell = AgentCell(
                id=f"{kind}-custom-worker",
                name=name,
                group=group,
                cell_type="agent",
                kind=kind,
                directory=launch_cfg.get("directory", ""),
                profile=launch_cfg.get("profile", ""),
                command=launch_cfg.get("command", ""),
            )
            cell.agent_class_id = launch_cfg.get("agent_class_id", "")
            cell.agent_class_version = launch_cfg.get("agent_class_version", "")
            if cell.agent_class_id:
                cell.agent_class_assigned_at = time.time()
                cell.agent_class_assigned_by = "trusted-user-launch"
            self.state.agents[cell.id] = cell
            self.state.groups.setdefault(group, []).append(cell.id)
            self.state.apply_effective_agent_class_for_launch(
                cell,
                base_dir=cell.directory,
            )
            persistent_prompt_text = append_agent_class_prompt_block(
                kwargs.get("persistent_prompt_text", ""),
                cell,
            )
            created_prompts[cell.id] = persistent_prompt_text
            self.state._db_save_agent(cell)
            return cell

        async def send_agent_prompt(*_args, **_kwargs):
            return None

        async def run_flow():
            draft_payload = {
                "id": "qa-worker-draft",
                "version": "1",
                "base_kind": "worker",
                "display_name": "QA Worker Draft",
                "description": "Draft validation-only worker class.",
                "lifecycle": "draft",
                "draft": {"scratch_only": True},
                "agent_profile_ref": {"id": "full-worker", "version": "1"},
            }
            custom_payload = {
                "id": "qa-worker",
                "version": "1",
                "base_kind": "worker",
                "display_name": "QA Worker",
                "description": "Validation worker class.",
                "agent_profile_ref": {"id": "full-worker", "version": "1"},
                "prompt": {"job": "Check the assignment and report evidence first."},
                "icon": "QA",
                "badge": "validation",
            }
            draft_validation = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_validate",
                    "base_dir": str(self.project),
                    "agent_class": draft_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            validation = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_validate",
                    "request_id": "validate-qa-worker",
                    "base_dir": str(self.project),
                    "agent_class": custom_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            created = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_create",
                    "request_id": "create-qa-worker",
                    "base_dir": str(self.project),
                    "agent_class": custom_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            listed = await _handle_agent_class_command(
                {"cmd": "agent_class_list", "base_dir": str(self.project)},
                self.state,
                self.db,
                resolve_base_dir,
            )
            preview = await _handle_agent_class_command(
                {"cmd": "agent_class_preview", "class_id": "qa-worker", "base_dir": str(self.project)},
                self.state,
                self.db,
                resolve_base_dir,
            )
            updated_payload = dict(custom_payload)
            updated_payload["description"] = "Validation worker class updated."
            updated_payload["prompt"] = {"job": "Updated prompt: verify custom worker launch evidence."}
            updated = await _handle_agent_class_command(
                {
                    "cmd": "agent_class_update",
                    "request_id": "update-qa-worker",
                    "base_dir": str(self.project),
                    "agent_class": updated_payload,
                },
                self.state,
                self.db,
                resolve_base_dir,
            )
            launched = await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "qa-worker",
                    "name": "QA Launch Worker",
                    "group": "g",
                    "base_dir": str(self.project),
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )
            ambiguous = await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "qa-worker",
                    "agent_profile_id": "full-worker",
                    "name": "Ambiguous QA Worker",
                    "group": "g",
                    "base_dir": str(self.project),
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )
            mismatch = await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "qa-worker",
                    "kind": "engineer",
                    "name": "Wrong Kind",
                    "group": "g",
                    "base_dir": str(self.project),
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )
            return {
                "draft_validation": draft_validation,
                "validation": validation,
                "created": created,
                "listed": listed,
                "preview": preview,
                "updated": updated,
                "launched": launched,
                "ambiguous": ambiguous,
                "mismatch": mismatch,
            }

        results = asyncio.run(run_flow())

        draft_warning_text = "\n".join(str(item) for item in results["draft_validation"]["warnings"])
        self.assertTrue(results["draft_validation"]["valid"], results["draft_validation"])
        self.assertIn("lifecycle=draft", draft_warning_text)
        self.assertIn("External connector exposure is not enforced", draft_warning_text)
        self.assertTrue(results["validation"]["valid"], results["validation"])
        self.assertEqual(results["validation"]["request_id"], "validate-qa-worker")
        self.assertEqual(results["validation"]["agent_class"]["agent_profile_ref"], {"id": "full-worker", "version": "1"})
        self.assertFalse(results["validation"]["agent_class"]["apply_state"]["mutates_running_sessions"])
        self.assertIn("External connector", results["validation"]["agent_class"]["external_connector_caveat"])
        self.assertEqual(results["created"]["operation"], "created")
        self.assertTrue(results["created"]["ok"], results["created"])
        listed_ids = [item["id"] for item in results["listed"]["classes"]]
        self.assertIn("qa-worker", listed_ids)
        self.assertLess(listed_ids.index("qa-worker"), listed_ids.index("default-worker"))
        preview = results["preview"]["agent_class"]
        self.assertEqual(preview["primary_identity_label"], "QA Worker")
        self.assertEqual(preview["secondary_base_kind_label"], "Worker-derived")
        self.assertEqual(preview["internal_policy"]["mode"], "wrap_profile")
        self.assertEqual(preview["operator_access_summary"]["allowed_summary"], "Wrapped internal Agent Profile policy")
        self.assertEqual(results["updated"]["operation"], "updated")

        launched = results["launched"]
        self.assertEqual(launched["type"], "agent_class_launch")
        self.assertEqual(launched["schema_version"], 4)
        self.assertFalse(launched["storage"]["mutates_running_sessions"])
        self.assertEqual(launched["storage"]["launch_boundary"], "new_agent")
        self.assertEqual(launched["base_kind"], "worker")
        self.assertEqual(launched["agent"]["kind"], "worker")
        self.assertEqual(launched["agent"]["agent_class_status"]["effective_class_id"], "qa-worker")
        self.assertEqual(launched["agent"]["agent_profile_status"]["effective_profile_id"], "full-worker")

        cell = self.state.agents["worker-custom-worker"]
        self.assertEqual(cell.agent_class_id, "qa-worker")
        self.assertEqual(cell.agent_class_assigned_by, "trusted-user-launch")
        self.assertEqual(cell.effective_agent_class_id, "qa-worker")
        self.assertEqual(cell.effective_agent_profile_id, "full-worker")
        self.assertEqual(cell.effective_agent_class_snapshot["primary_identity_label"], "QA Worker")
        self.assertEqual(
            cell.effective_agent_class_snapshot["prompt"],
            {"job": "Updated prompt: verify custom worker launch evidence."},
        )
        self.assertIn("## Agent Class", created_prompts[cell.id])
        self.assertIn("QA Worker", created_prompts[cell.id])
        self.assertIn("Updated prompt: verify custom worker launch evidence.", created_prompts[cell.id])

        policy = profile_policy_from_definition(cell.effective_agent_profile_snapshot)
        self.assertTrue(policy.is_full_base_kind_profile)
        self.assertTrue(mcp_tool_allowed_by_policy("torque_context", policy))
        self.assertTrue(mcp_tool_allowed_by_policy("torque_verify", policy))

        projected = {
            item["category"]: item
            for item in cell.effective_agent_profile_snapshot["projected_tool_categories"]
        }
        self.assertEqual(projected["context_read"]["status"], "allowed")
        for denied_category in {
            "worker_dispatch",
            "execution_task_control",
            "worktree_merge",
            "deploy_admin",
            "profile_admin",
            "engineer_worker_comm",
        }:
            self.assertEqual(projected[denied_category]["status"], "denied")

        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["agent_class_id"], "qa-worker")
        self.assertEqual(loaded["effective_agent_class_snapshot"]["id"], "qa-worker")
        self.assertEqual(loaded["effective_agent_profile_snapshot"]["id"], "full-worker")
        self.assertEqual(results["ambiguous"]["type"], "error")
        self.assertEqual(results["ambiguous"]["code"], "ambiguous_agent_class_profile_launch")
        self.assertEqual(results["mismatch"]["type"], "error")
        self.assertEqual(results["mismatch"]["code"], "agent_class_base_kind_mismatch")

    def test_torque_steward_launch_defaults_to_stable_identity_and_read_only_policy(self):
        from torque.server import _handle_agent_class_launch_command

        async def resolve_base_dir(_group):
            return str(self.project)

        def resolve_launch_config(_group, *, base_dir="", explicit_template="", overrides=None):
            del explicit_template, overrides
            return {
                "profile": "Default",
                "command": "codex",
                "directory": base_dir or str(self.project),
                "tab_color": "",
                "icon": "",
                "env_vars": {},
                "env_file": "",
                "shell": "",
                "system_prompt": "",
                "agent_type": "codex",
                "session_resume": True,
                "idle_timeout": 0,
                "worktree": False,
                "worktree_base_dir": ".torque/worktrees",
                "worktree_auto_checkpoint": False,
                "checkpoint_on_progress": False,
                "worktree_merge_squash": True,
                "terminals": [],
            }

        created_prompt = {}

        async def create_agent_with_config(group, name, launch_cfg, **kwargs):
            kind = kwargs.get("kind", "")
            created_prompt["persistent_prompt_text"] = kwargs.get("persistent_prompt_text", "")
            cell = AgentCell(
                id=f"{kind}-steward",
                name=name,
                group=group,
                cell_type="agent",
                kind=kind,
                directory=launch_cfg.get("directory", ""),
                profile=launch_cfg.get("profile", ""),
                command=launch_cfg.get("command", ""),
            )
            cell.agent_class_id = launch_cfg.get("agent_class_id", "")
            cell.agent_class_version = launch_cfg.get("agent_class_version", "")
            self.state.agents[cell.id] = cell
            self.state.groups.setdefault(group, []).append(cell.id)
            self.state.apply_effective_agent_class_for_launch(
                cell,
                base_dir=cell.directory,
            )
            self.state._db_save_agent(cell)
            return cell

        async def send_agent_prompt(*_args, **_kwargs):
            return None

        async def run_launch():
            return await _handle_agent_class_launch_command(
                {
                    "cmd": "create_agent_from_class",
                    "class_id": "torque-steward",
                    "name": "Custom Steward Name",
                    "group": "g",
                },
                self.state,
                resolve_base_dir=resolve_base_dir,
                resolve_agent_launch_config=resolve_launch_config,
                resolve_engineer_launch_config=resolve_launch_config,
                resolve_architect_launch_config=resolve_launch_config,
                resolve_worker_launch_config=resolve_launch_config,
                create_agent_with_config=create_agent_with_config,
                specialization_mgr=None,
                send_agent_prompt=send_agent_prompt,
            )

        launched = asyncio.run(run_launch())

        self.assertEqual(launched["type"], "agent_class_launch")
        self.assertEqual(launched["agent"]["name"], "Custom Steward Name")
        self.assertEqual(launched["agent"]["kind"], "architect")
        cell = self.state.agents["architect-steward"]
        self.assertEqual(cell.name, "Custom Steward Name")
        self.assertEqual(cell.effective_agent_class_id, "torque-steward")
        self.assertEqual(cell.effective_agent_profile_id, "class-policy-torque-steward")
        self.assertEqual(cell.effective_agent_class_snapshot.get("acl", {}).get("mode"), "allow")
        self.assertIn("architect_steward_operating_brief", cell.effective_agent_class_snapshot.get("acl", {}).get("allowed_tools", []))
        projected = {
            item.get("category"): item.get("status")
            for item in cell.effective_agent_profile_snapshot.get("projected_tool_categories", [])
        }
        self.assertEqual(projected.get("worker_dispatch"), "denied")
        self.assertEqual(projected.get("execution_task_control"), "denied")
        self.assertEqual(projected.get("deploy_admin"), "denied")
        self.assertEqual(projected.get("profile_admin"), "denied")
        self.assertEqual(projected.get("planning_reads"), "allowed")
        self.assertEqual(projected.get("peer_architect_comm"), "allowed")
        self.assertIn("Torque Steward", created_prompt["persistent_prompt_text"])
        self.assertIn("projected tool surface defined by your Agent Class ACL/capabilities", created_prompt["persistent_prompt_text"])
        self.assertIn("visible user-message tool", created_prompt["persistent_prompt_text"])
        self.assertNotIn("architect_product_message_user", created_prompt["persistent_prompt_text"])


if __name__ == "__main__":
    unittest.main()
