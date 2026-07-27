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
    delete_custom_agent_class,
    enriched_agent_class_preview,
    freeze_agent_class_snapshot,
    load_agent_classes,
    render_agent_class_prompt,
    save_custom_agent_class,
    validate_class_data,
    validate_agent_class_draft,
)
from torque.capability_catalog import CAPABILITY_CATALOG
from torque.mcp import (
    mcp_tool_allowed_by_authority,
)
from torque.mcp_authority import compile_agent_class_acl, effective_authority_from_snapshot
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState


class AgentClassRuntimeGenericityTests(unittest.TestCase):
    def test_production_python_does_not_recognize_builtin_class_identities(self):
        root = Path(__file__).resolve().parents[1] / "torque"
        forbidden = (
            "creative-architect",
            "product-manager",
            "torque-steward",
            "is_creative",
            "is_product",
            "is_steward",
            "architect_product_",
            "class-policy-",
        )
        matches = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    matches.append(f"{path.relative_to(root)}: {token}")
        self.assertEqual(matches, [])

    def test_different_class_ids_compile_identical_prompt_and_authority(self):
        common = {
            "agent_class_schema_version": 5,
            "version": "1",
            "base_kind": "architect",
            "lifecycle": "stable",
            "prompt": {
                "identity": "You are a generic planning partner.",
                "job": "Produce bounded proposals.",
                "tool_guidance": [{
                    "when_capability": "task.read",
                    "text": "Read visible tasks before proposing changes.",
                }],
            },
            "acl": {
                "mode": "allow",
                "rules": [
                    {"capability": "self.read", "scope": "self"},
                    {"capability": "task.read", "scope": "group"},
                ],
            },
        }
        definitions = []
        for class_id in ("planning-alpha", "planning-beta"):
            definition, issues = validate_class_data({
                **common,
                "id": class_id,
                "display_name": class_id.replace("-", " ").title(),
            })
            self.assertEqual(issues, [])
            definitions.append(definition)

        snapshots = [
            freeze_agent_class_snapshot(
                item,
                assignment_source="test",
                frozen_at=1.0,
            )
            for item in definitions
        ]
        self.assertEqual(
            snapshots[0]["effective_authority"],
            snapshots[1]["effective_authority"],
        )
        rendered = [
            render_agent_class_prompt(item.prompt, snapshot=snapshot)
            for item, snapshot in zip(definitions, snapshots)
        ]
        self.assertEqual(rendered[0], rendered[1])


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

    def test_builtin_classes_use_schema_v5_capability_acls(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))

        self.assertEqual([], [issue.as_dict() for issue in issues])
        by_id = {definition.id: definition for definition in classes}
        expected = {
            "creative-architect": "architect",
            "default-architect": "architect",
            "default-engineer": "engineer",
            "default-worker": "worker",
            "product-manager": "architect",
            "torque-steward": "architect",
        }
        self.assertEqual(set(expected), set(by_id))
        for class_id, base_kind in expected.items():
            definition = by_id[class_id]
            self.assertTrue(definition.builtin)
            self.assertEqual(definition.agent_class_schema_version, 5)
            self.assertEqual(definition.base_kind, base_kind)

        for class_id in {
            "creative-architect",
            "product-manager",
            "torque-steward",
        }:
            rules = by_id[class_id].acl.get("rules", [])
            grouped = [
                rule for rule in rules
                if isinstance(rule, dict) and "capabilities" in rule
            ]
            self.assertGreaterEqual(
                len(grouped),
                2,
                f"{class_id} should author repeated scopes as grouped rules",
            )

    def test_legacy_engineer_message_capability_migrates_acl_and_guidance(self):
        definition, issues = validate_class_data({
            "agent_class_schema_version": 5,
            "id": "legacy-engineer-messaging",
            "version": "1",
            "display_name": "Legacy Engineer Messaging",
            "base_kind": "engineer",
            "lifecycle": "stable",
            "prompt": {
                "identity": "Coordinate engineering work.",
                "job": "Keep peers and the supervisor aligned.",
                "tool_guidance": [{
                    "when_capability": "message.engineer",
                    "text": "Use the eligible relationship channel.",
                }],
            },
            "acl": {
                "mode": "allow",
                "rules": [{
                    "capability": "message.engineer",
                    "scope": "group",
                }],
            },
        })

        self.assertEqual(issues, [])
        self.assertIsNotNone(definition)
        rules = definition.acl["rules"]
        self.assertIn(
            {"capability": "message.peer", "scope": "group"},
            rules,
        )
        self.assertIn(
            {"capability": "message.supervisor", "scope": "self"},
            rules,
        )
        selectors = {
            item["when_capability"]
            for item in definition.prompt["tool_guidance"]
        }
        self.assertEqual(
            selectors,
            {"message.peer", "message.supervisor"},
        )

    def test_product_manager_preview_is_class_first_capability_acl_with_caveat(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        pm = next(definition for definition in classes if definition.id == "product-manager")

        preview = enriched_agent_class_preview(pm, base_dir=str(self.project))

        self.assertEqual(preview["status"], "restricted")
        self.assertEqual(preview["display_name"], "Product Manager")
        self.assertEqual(preview["primary_identity_label"], "Product Manager")
        self.assertEqual(preview["secondary_base_kind_label"], "Architect-derived")
        self.assertEqual(preview["lifecycle"], "stable")
        self.assertNotIn("internal_policy", preview)
        self.assertEqual(
            preview["effective_authority"]["capabilities"]["task.propose"],
            "self",
        )
        for capability in (
                "task.read", "task.update", "task.move", "task.mark_covered",
                "task.verify", "task.reassign", "task.report"):
            self.assertEqual(
                preview["effective_authority"]["capabilities"][capability],
                "group",
                capability,
            )
        for capability in ("task.dispatch", "engineer.hire", "worktree.merge", "deploy.read"):
            self.assertNotIn(
                capability,
                preview["effective_authority"]["capabilities"],
                capability,
            )
        self.assertEqual(
            preview["effective_authority"]["capabilities"]["message.peer"],
            "group",
        )
        self.assertIn("capability_catalog", preview["authoring_contract"])
        self.assertEqual(preview["draft"], {})
        self.assertTrue(preview["metadata"]["approved_for_live_dogfood"])
        self.assertEqual(preview["metadata"]["permanence_state"], "dogfood_permanent")
        self.assertEqual(preview["acl"]["mode"], "allow")
        self.assertIn("decision.propose", preview["acl"]["capabilities"])
        self.assertEqual(preview["authority_summary"]["mode"], "allow")
        self.assertIn("external_connector_caveat", preview)
        self.assertNotIn(
            "class.admin",
            preview["effective_authority"]["capabilities"],
        )

    def test_creative_architect_preview_is_proposal_only_thinking_class(self):
        classes, issues = load_agent_classes(base_dir=str(self.project))
        self.assertFalse(issues)
        creative = next(definition for definition in classes if definition.id == "creative-architect")

        preview = enriched_agent_class_preview(creative, base_dir=str(self.project))

        self.assertEqual(preview["status"], "restricted")
        self.assertEqual(preview["display_name"], "Creative")
        self.assertEqual(preview["primary_identity_label"], "Creative")
        self.assertEqual(preview["secondary_base_kind_label"], "Architect-derived")
        self.assertTrue(preview["metadata"]["proposal_only"])
        self.assertEqual(preview["acl"]["mode"], "allow")
        for capability in {
            "thinking.read",
            "thinking.write",
            "idea_brief.read",
            "idea_brief.write",
            "idea_brief.propose",
            "decision.propose",
            "task.propose",
        }:
            self.assertIn(capability, preview["effective_authority"]["capabilities"])
        warnings = "\n".join(preview["warnings"])
        self.assertIn("proposal-only", warnings)
        self.assertIn("canonical Thinking and Idea Brief tools", warnings)

        self.assertNotIn(
            "class.admin",
            preview["effective_authority"]["capabilities"],
        )

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
        self.assertTrue(preview["metadata"]["represents_user_wishes"])
        self.assertFalse(preview["metadata"]["auto_create_enabled"])
        self.assertEqual(preview["metadata"]["mutating_authority"], "none")
        self.assertEqual(preview["acl"]["mode"], "allow")
        self.assertEqual(preview["acl"]["capabilities"]["telemetry.read"], "group")
        self.assertEqual(preview["acl"]["capabilities"]["message.user"], "self")
        self.assertEqual(preview["authority_summary"]["mode"], "allow")
        self.assertEqual(preview["authority_summary"]["high_risk_capabilities"], [])
        warnings = "\n".join(preview["warnings"])
        self.assertIn("conservative operations steward", warnings)
        self.assertIn("conservative operations steward", warnings)


    def test_schema_v5_accepts_flat_and_grouped_capability_rules(self):
        valid = {
            "agent_class_schema_version": 5,
            "id": "planning-architect",
            "version": "1",
            "display_name": "Planning Architect",
            "identity": {"label": "Planning Architect", "primary_ui_label": "Planning Architect"},
            "runtime": {"base_kind": "architect", "base_kind_label": "Architect-derived"},
            "prompt": {"job": "Use planning-safe surfaces only."},
            "acl": {
                "mode": "allow",
                "rules": [
                    {
                        "scope": "self",
                        "capabilities": ["self.read", "message.user"],
                    },
                    {
                        "scope": "group",
                        "capabilities": ["planning.area.read"],
                    },
                ],
            },
            "warnings": ["External connectors are separate."],
        }

        definition, issues = validate_class_data(valid, base_dir=str(self.project))

        self.assertIsNotNone(definition, [issue.as_dict() for issue in issues])
        self.assertEqual(definition.agent_class_schema_version, 5)
        self.assertEqual(definition.base_kind, "architect")
        preview = enriched_agent_class_preview(definition, base_dir=str(self.project))
        self.assertEqual(
            set(preview["effective_authority"]["capabilities"]),
            {"self.read", "planning.area.read", "message.user"},
        )
        self.assertIn("capabilities", definition.acl["rules"][0])

        invalid = dict(valid)
        invalid["id"] = "bad-planning"
        invalid["acl"] = {"mode": "allow", "rules": [{"tool": "architect_task_create"}]}
        invalid["tools"] = ["architect_task_create"]
        _definition, invalid_issues = validate_class_data(invalid, base_dir=str(self.project))
        codes = {issue.code for issue in invalid_issues}
        self.assertIn("raw_tool_fields_forbidden", codes)

        mixed = dict(valid)
        mixed["id"] = "mixed-planning"
        mixed["acl"] = {"mode": "allow", "allow": [], "deny": []}
        _mixed_definition, mixed_issues = validate_class_data(mixed, base_dir=str(self.project))
        self.assertIn("invalid_capability_acl", {issue.code for issue in mixed_issues})

        legacy_prompt = dict(valid)
        legacy_prompt["id"] = "legacy-prompt"
        legacy_prompt["prompt"] = {"addendum": "Legacy addendum is not accepted."}
        _legacy_definition, legacy_issues = validate_class_data(legacy_prompt, base_dir=str(self.project))
        self.assertIn("unknown_prompt_fields", {issue.code for issue in legacy_issues})

    def test_capability_validation_rejects_cross_base_and_scope_broadening(self):
        worker_with_architect_bucket = {
            "agent_class_schema_version": 5,
            "id": "overwide-worker",
            "version": "1",
            "display_name": "Overwide Worker",
            "runtime": {"base_kind": "worker"},
            "acl": {"mode": "allow", "rules": [{"capability": "engineer.hire", "scope": "children"}]},
        }

        _definition, issues = validate_class_data(worker_with_architect_bucket, base_dir=str(self.project))
        codes = {issue.code for issue in issues}
        self.assertIn("invalid_capability_acl", codes)


    def test_custom_architect_acls_cannot_author_pm_group_board_extension(self):
        capabilities = (
            "task.update", "task.move", "task.mark_covered", "task.verify",
            "task.reassign", "task.report",
        )
        bare_deny = compile_agent_class_acl(
            base_kind="architect",
            acl={"mode": "deny", "rules": []},
            capabilities=CAPABILITY_CATALOG,
        )
        self.assertEqual("self", bare_deny.capabilities["task.update"])
        self.assertEqual("children", bare_deny.capabilities["task.reassign"])
        with self.assertRaisesRegex(ValueError, "exceeds the architect ceiling"):
            compile_agent_class_acl(
                base_kind="architect",
                acl={
                    "mode": "allow",
                    "rules": [
                        {"capability": capability, "scope": "group"}
                        for capability in capabilities
                    ],
                },
                capabilities=CAPABILITY_CATALOG,
            )

    def test_invalid_config_rejects_raw_tools_terminal_profile_confusion_and_bad_draft(self):
        _definition, issues = validate_class_data(
            {
                "id": "bad-class",
                "version": "1",
                "base_kind": "architect",
                "profile": "Default",
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

    def test_authoring_validate_save_update_archive_delete_project_yaml(self):
        draft = {
            "agent_class_schema_version": 5,
            "id": "custom-architect",
            "version": "1",
            "base_kind": "architect",
            "title": "Custom Architect",
            "description": "Operator-authored class.",
            "acl": {"mode": "deny", "rules": []},
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
        self.assertEqual(preview["effective_authority"]["acl_mode"], "deny")

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

    def test_authoring_capability_acl_contract_has_no_policy_or_bucket_fields(self):
        draft = {
            "id": "pm-lite",
            "version": "1",
            "title": "PM Lite",
            "purpose": "Product planning readout and user updates.",
            "base_kind": "architect",
            "agent_class_schema_version": 5,
            "acl": {
                "mode": "allow",
                "rules": [
                    {"capability": "self.read", "scope": "self"},
                    {"capability": "board.read", "scope": "group"},
                    {"capability": "message.user", "scope": "self"},
                ],
            },
        }

        validation = validate_agent_class_draft(draft, base_dir=str(self.project))

        self.assertTrue(validation["valid"], validation)
        normalized = validation["normalized"]
        self.assertEqual(normalized["description"], "Product planning readout and user updates.")
        self.assertNotIn("policy", normalized)
        self.assertNotIn("capabilities", normalized)
        self.assertIn("capability_catalog", validation["authoring_contract"])
        self.assertEqual(
            validation["authoring_contract"]["acl_rule_variants"],
            {
                "single": ["capability", "scope"],
                "grouped_by_scope": ["scope", "capabilities"],
            },
        )
        preview = validation["agent_class"]
        self.assertNotIn("internal_policy", preview)
        self.assertEqual(set(preview["acl"]["capabilities"]), {"self.read", "board.read", "message.user"})
        self.assertTrue(preview["apply_state"]["relaunch_required_after_assignment"])
        self.assertEqual(preview["runtime_enforcement"], "launch_frozen_effective_authority")

        saved = save_custom_agent_class(draft, base_dir=str(self.project), mode="create")
        self.assertTrue(saved["ok"], saved)
        path = Path(saved["storage"]["path"])
        saved_yaml = path.read_text(encoding="utf-8")
        self.assertIn("rules:", saved_yaml)

    def test_authoring_rejects_forbidden_fields_before_persistence(self):
        invalid = {
            "id": "bad-custom",
            "version": "1",
            "base_kind": "architect",
            "display_name": "Bad Custom",
            "agent_class_schema_version": 5,
            "acl": {"mode": "allow", "rules": []},
            "metadata": {"mcp_tools": ["architect_task_create"]},
            "runtime_profile": "Default",
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
        self.assertFalse((self.root / "repo" / ".torque" / "agent_classes" / "bad-custom.yaml").exists())

    def test_custom_class_save_preserves_grouped_acl_authoring_shape(self):
        result = save_custom_agent_class({
            "id": "acl-architect",
            "version": "1",
            "agent_class_schema_version": 5,
            "base_kind": "architect",
            "display_name": "ACL Architect",
            "acl": {
                "mode": "allow",
                "rules": [
                    {
                        "scope": "self",
                        "capabilities": [
                            "self.read",
                            "task.report",
                            "message.user",
                        ],
                    },
                ],
            },
        }, base_dir=str(self.project), mode="create")

        self.assertTrue(result["ok"], result)
        saved = Path(result["storage"]["path"]).read_text(encoding="utf-8")
        self.assertIn("acl:", saved)
        self.assertIn("mode: allow", saved)
        self.assertIn("scope: self", saved)
        self.assertIn("capabilities:", saved)
        self.assertIn("- self.read", saved)
        self.assertIn("- message.user", saved)
        self.assertNotIn("capability: self.read", saved)
        self.assertNotIn("policy:", saved)
        definition = agent_class_definition_by_id("acl-architect", base_dir=str(self.project))
        self.assertIsNotNone(definition)
        self.assertEqual(definition.acl.get("mode"), "allow")
        self.assertEqual(
            definition.acl["rules"][0]["capabilities"],
            ["self.read", "task.report", "message.user"],
        )
        preview = enriched_agent_class_preview(definition, base_dir=str(self.project))
        self.assertEqual(
            set(preview["effective_authority"]["capabilities"]),
            {"self.read", "task.report", "message.user"},
        )

    def test_project_config_path_duplicate_and_docs(self):
        self._write_project_class(
            "default-worker.yaml",
            "\n".join([
                "id: default-worker",
                'version: "1"',
                "base_kind: worker",
                "display_name: Default Worker Override",
                "agent_class_schema_version: 5",
                "acl:",
                "  mode: deny",
                "  rules: []",
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
        self.assertTrue(status["pending_next_launch"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["agent_class_id"], "product-manager")
        audit = self.db.list_agent_class_audit(agent_id=cell.id)
        self.assertEqual(audit[0]["event"], "assignment_set")

    def test_launch_freezes_class_effective_authority_snapshot(self):
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
        self.assertNotIn("internal_policy", snapshot)
        self.assertEqual(snapshot["status"], "restricted")
        self.assertEqual(snapshot["acl"]["mode"], "allow")
        self.assertEqual(snapshot["effective_authority"]["capabilities"]["task.propose"], "self")
        self.assertEqual(cell.effective_agent_class_id, "product-manager")
        self.assertTrue(snapshot["snapshot_hash"])
        self.assertFalse(
            self.state.agent_class_status_for_cell(cell, base_dir=str(self.project))["pending_next_launch"]
        )
        audit = self.db.list_agent_class_audit(agent_id=cell.id)
        self.assertEqual(audit[0]["event"], "effective_snapshot_applied")
        self.assertTrue(audit[0]["snapshot_hash"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["effective_agent_class_snapshot"]["id"], "product-manager")

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
        cell.effective_agent_class_snapshot["version"] = "2"

        class_status = self.state.agent_class_status_for_cell(
            cell,
            base_dir=str(self.project),
        )
        self.assertEqual(class_status["assigned_class_version"], "2")
        self.assertEqual(class_status["effective_class_version"], "2")
        self.assertEqual(class_status["next_launch_class_version"], "6")
        self.assertTrue(class_status["pending_next_launch"])
        self.assertTrue(class_status["apply_state"]["relaunch_required"])


    def test_class_driven_authority_projects_semantic_pm_capabilities(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "product-manager",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        authority = effective_authority_from_snapshot(
            cell.effective_agent_class_snapshot["effective_authority"],
            capabilities=CAPABILITY_CATALOG,
        )

        self.assertTrue(mcp_tool_allowed_by_authority("architect_proposal_board_summary", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_board_summary", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_idea_brief_create", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_task_create", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_engineer_hire", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("tool_search", authority))

    def test_torque_steward_class_projection_allows_discovery_and_denies_mutations(self):
        cell = self._add_agent(kind="architect")
        self.state.assign_agent_class(
            cell.id,
            "torque-steward",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

        authority = effective_authority_from_snapshot(
            cell.effective_agent_class_snapshot["effective_authority"],
            capabilities=CAPABILITY_CATALOG,
        )

        for allowed in (
            "tool_search",
            "architect_board_summary",
            "architect_group_health_brief",
            "architect_events_recent",
            "architect_task_show",
            "architect_area_list",
            "architect_initiative_list",
            "architect_decision_list",
            "architect_mcp_calls",
            "architect_ask",
            "architect_message_user",
            "architect_peer_list",
            "architect_peer_message",
            "architect_journal",
            "architect_journal_read",
        ):
            self.assertTrue(mcp_tool_allowed_by_authority(allowed, authority), allowed)
        for denied in (
            "architect_engineer_message",
            "architect_engineer_hire",
            "architect_task_create",
            "architect_task_update",
            "architect_task_move",
            "architect_deploy_state",
            "architect_get_architect_settings",
            "architect_behavior_overlay_read",
            "architect_decision_create",
            "architect_decision_update",
            "engineer_merge",
        ):
            self.assertFalse(mcp_tool_allowed_by_authority(denied, authority), denied)

    def test_creative_architect_launch_freezes_prompt_and_capability_authority(self):
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
        authority = effective_authority_from_snapshot(
            snapshot["effective_authority"],
            capabilities=CAPABILITY_CATALOG,
        )
        prompt_block = agent_class_prompt_block_for_cell(cell)

        self.assertEqual(snapshot["id"], "creative-architect")
        self.assertEqual(snapshot["display_name"], "Creative")
        self.assertEqual(snapshot["primary_identity_label"], "Creative")
        self.assertEqual(cell.effective_agent_class_id, "creative-architect")
        self.assertIn("imaginative but grounded ideation partner", prompt_block)
        self.assertIn("Diverge first", prompt_block)
        self.assertIn("non-binding until accepted", prompt_block)
        self.assertTrue(mcp_tool_allowed_by_authority("architect_thinking_scratchpad_create", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_thinking_mind_map_node_create", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_idea_brief_create", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_idea_brief_propose", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_task_propose", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_board_summary", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("tool_search", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_peer_message", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_message_user", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_decision_link", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_engineer_hire", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_task_create", authority))

        cell.effective_agent_class_snapshot["display_name"] = "Creative Architect"
        cell.effective_agent_class_snapshot["primary_identity_label"] = "Creative Architect"
        status = self.state.agent_class_status_for_cell(cell, base_dir=str(self.project))
        self.assertEqual(status["effective_primary_identity_label"], "Creative")
        self.assertEqual(status["next_launch_primary_identity_label"], "Creative")

    def test_default_unassigned_classes_preserve_full_base_kind_authority_and_no_prompt(self):
        for kind, expected_class in [
            ("architect", "default-architect"),
            ("engineer", "default-engineer"),
            ("worker", "default-worker"),
        ]:
            cell = self._add_agent(kind=kind, agent_id=f"{kind}-1")

            self.state.apply_effective_agent_class_for_launch(cell, base_dir=str(self.project))

            self.assertEqual(cell.effective_agent_class_id, expected_class)
            authority = effective_authority_from_snapshot(
                cell.effective_agent_class_snapshot["effective_authority"],
                capabilities=CAPABILITY_CATALOG,
            )
            self.assertTrue(authority.capabilities)
            self.assertEqual(authority.mode, "deny")
            if kind == "architect":
                self.assertNotIn("task.dispatch", authority.capabilities)
            elif kind == "engineer":
                self.assertEqual("children", authority.capabilities["task.dispatch"])
            else:
                self.assertNotIn("task.dispatch", authority.capabilities)
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
        self.assertIn("Effective Torque MCP authority", prompt_block)
        self.assertIn("Propose queued tasks (`task.propose`)", prompt_block)
        self.assertEqual(context["id"], "product-manager")
        self.assertEqual(context["primary_identity_label"], "Product Manager")
        self.assertEqual(
            context["effective_authority"]["capabilities"]["task.propose"],
            "self",
        )
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
            custom_payload = {
                "agent_class_schema_version": 5,
                "id": "panel-architect",
                "version": "1",
                "base_kind": "architect",
                "display_name": "Panel Architect",
                "acl": {"mode": "deny", "rules": []},
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
        self.assertIn("capability_catalog", listed)
        self.assertIn("self.read", {item["id"] for item in listed["capability_catalog"]})
        self.assertIn("product-manager", {item["id"] for item in listed["classes"]})
        self.assertIn("panel-architect", {item["id"] for item in listed["classes"]})
        self.assertEqual(preview["agent_class"]["primary_identity_label"], "Product Manager")
        self.assertIn("effective_authority", preview["agent_class"])
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
                "agent_class_schema_version": 5,
                "id": "launch-architect",
                "version": "1",
                "base_kind": "architect",
                "display_name": "Launch Architect",
                "acl": {"mode": "deny", "rules": []},
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
                "agent_class_schema_version": 5,
                "id": "qa-worker-draft",
                "version": "1",
                "base_kind": "worker",
                "display_name": "QA Worker Draft",
                "description": "Draft validation-only worker class.",
                "lifecycle": "draft",
                "draft": {"scratch_only": True},
                "acl": {"mode": "deny", "rules": []},
            }
            custom_payload = {
                "agent_class_schema_version": 5,
                "id": "qa-worker",
                "version": "1",
                "base_kind": "worker",
                "display_name": "QA Worker",
                "description": "Validation worker class.",
                "acl": {"mode": "deny", "rules": []},
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
                "mismatch": mismatch,
            }

        results = asyncio.run(run_flow())

        draft_warning_text = "\n".join(str(item) for item in results["draft_validation"]["warnings"])
        self.assertTrue(results["draft_validation"]["valid"], results["draft_validation"])
        self.assertIn("lifecycle=draft", draft_warning_text)
        self.assertIn("External connector exposure is not enforced", draft_warning_text)
        self.assertTrue(results["validation"]["valid"], results["validation"])
        self.assertEqual(results["validation"]["request_id"], "validate-qa-worker")
        self.assertEqual(
            results["validation"]["agent_class"]["effective_authority"]["acl_mode"],
            "deny",
        )
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
        self.assertNotIn("internal_policy", preview)
        self.assertEqual(preview["runtime_enforcement"], "launch_frozen_effective_authority")
        self.assertEqual(preview["status"], "full")
        self.assertEqual(results["updated"]["operation"], "updated")

        launched = results["launched"]
        self.assertEqual(launched["type"], "agent_class_launch")
        self.assertEqual(launched["schema_version"], 5)
        self.assertFalse(launched["storage"]["mutates_running_sessions"])
        self.assertEqual(launched["storage"]["launch_boundary"], "new_agent")
        self.assertEqual(launched["base_kind"], "worker")
        self.assertEqual(launched["agent"]["kind"], "worker")
        self.assertEqual(launched["agent"]["agent_class_status"]["effective_class_id"], "qa-worker")

        cell = self.state.agents["worker-custom-worker"]
        self.assertEqual(cell.agent_class_id, "qa-worker")
        self.assertEqual(cell.agent_class_assigned_by, "trusted-user-launch")
        self.assertEqual(cell.effective_agent_class_id, "qa-worker")
        self.assertEqual(cell.effective_agent_class_snapshot["primary_identity_label"], "QA Worker")
        self.assertEqual(
            cell.effective_agent_class_snapshot["prompt"],
            {"job": "Updated prompt: verify custom worker launch evidence."},
        )
        self.assertIn("## Agent Class", created_prompts[cell.id])
        self.assertIn("QA Worker", created_prompts[cell.id])
        self.assertIn("Updated prompt: verify custom worker launch evidence.", created_prompts[cell.id])

        authority = effective_authority_from_snapshot(
            cell.effective_agent_class_snapshot["effective_authority"],
            capabilities=CAPABILITY_CATALOG,
        )
        self.assertIsNotNone(authority)
        self.assertTrue(mcp_tool_allowed_by_authority("torque_context", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("torque_verify", authority))

        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["agent_class_id"], "qa-worker")
        self.assertEqual(loaded["effective_agent_class_snapshot"]["id"], "qa-worker")
        self.assertEqual(results["mismatch"]["type"], "error")
        self.assertEqual(results["mismatch"]["code"], "agent_class_base_kind_mismatch")

    def test_torque_steward_launch_defaults_to_stable_identity_and_read_only_authority(self):
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
        self.assertEqual(cell.effective_agent_class_snapshot.get("acl", {}).get("mode"), "allow")
        self.assertEqual(
            cell.effective_agent_class_snapshot["effective_authority"]["capabilities"]["telemetry.read"],
            "group",
        )
        authority = effective_authority_from_snapshot(
            cell.effective_agent_class_snapshot["effective_authority"],
            capabilities=CAPABILITY_CATALOG,
        )
        self.assertTrue(mcp_tool_allowed_by_authority("architect_area_list", authority))
        self.assertTrue(mcp_tool_allowed_by_authority("architect_peer_message", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_task_create", authority))
        self.assertFalse(mcp_tool_allowed_by_authority("architect_deploy_state", authority))
        self.assertIn("Torque Steward", created_prompt["persistent_prompt_text"])
        self.assertIn("Effective Torque MCP authority", created_prompt["persistent_prompt_text"])
        self.assertIn("## Wake-to-user status contract", created_prompt["persistent_prompt_text"])
        self.assertIn(
            "positively choose no message for unchanged state",
            created_prompt["persistent_prompt_text"],
        )
        self.assertNotIn("architect_proposal_message_user", created_prompt["persistent_prompt_text"])


if __name__ == "__main__":
    unittest.main()
