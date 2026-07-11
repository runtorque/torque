import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.agent_profiles import CAPABILITIES
from torque.capability_catalog import (
    CAPABILITY_CATALOG,
    LEGACY_ATOM_TO_CAPABILITY,
    canonical_capabilities_from_legacy_atoms,
    capability_catalog_for_base_kind,
    validate_capability_catalog,
)
from torque.mcp import (
    MCP_TOOL_AUTHORITY_DEFINITIONS,
    mcp_tool_allowed_by_authority,
)
from torque.mcp_authority import (
    AuthorityValidationError,
    CapabilityDefinition,
    authority_definition_from_tool_spec,
    audit_tool_authority_coverage,
    compile_agent_class_acl,
    evaluate_capability_acl,
    effective_authority_from_snapshot,
    next_narrower_scope,
    registry_hash,
    scope_includes,
)


class MCPAuthorityPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.capabilities = {
            "self.read": CapabilityDefinition(
                id="self.read",
                label="Read self",
                description="Read caller context.",
                base_kinds=frozenset({"worker", "engineer", "architect"}),
                scopes=("self",),
                ceilings={
                    "worker": "self",
                    "engineer": "self",
                    "architect": "self",
                },
            ),
            "message.engineer": CapabilityDefinition(
                id="message.engineer",
                label="Message Engineers",
                description="Message scoped Engineers.",
                risk="high",
                scopes=("children", "group", "global"),
                ceilings={"architect": "group"},
            ),
            "deploy.apply": CapabilityDefinition(
                id="deploy.apply",
                label="Deploy",
                description="Apply deployment changes.",
                risk="critical",
                base_kinds=frozenset({"architect"}),
            ),
        }

    def test_scope_order_and_narrowing(self):
        self.assertTrue(scope_includes("global", "self"))
        self.assertTrue(scope_includes("group", "children"))
        self.assertFalse(scope_includes("children", "group"))
        self.assertEqual(next_narrower_scope("group"), "children")
        self.assertEqual(
            next_narrower_scope("group", supported=("self", "group")),
            "self",
        )
        self.assertEqual(next_narrower_scope("self"), "")

    def test_canonical_capability_catalog_is_valid_and_class_agnostic(self):
        self.assertEqual(validate_capability_catalog(), [])
        self.assertIn("message.engineer", CAPABILITY_CATALOG)
        self.assertIn("task.dispatch", CAPABILITY_CATALOG)
        self.assertNotIn("product-manager", repr(CAPABILITY_CATALOG).lower())
        self.assertNotIn("creative", repr(CAPABILITY_CATALOG).lower())
        self.assertNotIn("steward", repr(CAPABILITY_CATALOG).lower())

    def test_catalog_exposes_only_base_kind_ceiling_and_supported_scopes(self):
        worker = {
            item["id"]: item
            for item in capability_catalog_for_base_kind("worker")
        }
        architect = {
            item["id"]: item
            for item in capability_catalog_for_base_kind("architect")
        }
        self.assertEqual(worker["task.read"]["maximum_scope"], "self")
        self.assertNotIn("engineer.hire", worker)
        self.assertEqual(
            architect["message.engineer"]["maximum_scope"],
            "children",
        )
        self.assertNotIn("task.dispatch", architect)

    def test_coverage_audit_reports_every_drift_type(self):
        report = audit_tool_authority_coverage(
            [
                {"name": "tool_a"},
                {"name": "tool_a"},
                {"name": "tool_b"},
            ],
            {
                "tool_a": {"known.capability"},
                "tool_stale": {"unknown.capability"},
                "tool_empty": set(),
            },
            known_capabilities={"known.capability"},
        )
        self.assertFalse(report.ok)
        self.assertEqual(report.unmapped_tools, ("tool_b",))
        self.assertEqual(
            report.stale_authority_tools,
            ("tool_empty", "tool_stale"),
        )
        self.assertEqual(report.duplicate_registered_tools, ("tool_a",))
        self.assertEqual(report.empty_requirements, ("tool_empty",))
        self.assertEqual(report.unknown_capabilities, ("unknown.capability",))
        with self.assertRaises(RuntimeError):
            report.require_valid()

    def test_allow_mode_starts_empty_and_grants_requested_scope(self):
        authority = evaluate_capability_acl(
            base_kind="architect",
            mode="allow",
            rules=[
                {"capability": "self.read", "scope": "self"},
                {"capability": "message.engineer", "scope": "children"},
            ],
            capabilities=self.capabilities,
        )
        self.assertTrue(authority.allows("self.read", scope="self"))
        self.assertTrue(
            authority.allows("message.engineer", scope="children")
        )
        self.assertFalse(authority.allows("message.engineer", scope="group"))
        self.assertFalse(authority.has("deploy.apply"))

    def test_deny_mode_starts_full_and_scoped_rule_narrows(self):
        authority = evaluate_capability_acl(
            base_kind="architect",
            mode="deny",
            rules=[
                {"capability": "message.engineer", "scope": "group"},
                {"capability": "deploy.apply"},
            ],
            capabilities=self.capabilities,
        )
        self.assertEqual(
            authority.capabilities["message.engineer"],
            "children",
        )
        self.assertFalse(authority.has("deploy.apply"))
        self.assertTrue(authority.allows("self.read", scope="self"))

    def test_acl_rejects_ambiguous_or_unsupported_rules(self):
        invalid_rules = [
            [{"capability": "missing"}],
            [{"capability": "message.engineer"}],
            [{"capability": "message.engineer", "scope": "global"}],
            [{"capability": "deploy.apply", "scope": "global"}],
            [
                {"capability": "deploy.apply"},
                {"capability": "deploy.apply"},
            ],
        ]
        for rules in invalid_rules:
            with self.subTest(rules=rules), self.assertRaises(
                AuthorityValidationError
            ):
                evaluate_capability_acl(
                    base_kind="architect",
                    mode="allow",
                    rules=rules,
                    capabilities=self.capabilities,
                )

    def test_effective_authority_snapshot_is_generic(self):
        authority = evaluate_capability_acl(
            base_kind="architect",
            mode="allow",
            rules=[{"capability": "deploy.apply"}],
            capabilities=self.capabilities,
        )
        self.assertEqual(
            authority.as_snapshot(),
            {
                "schema_version": 1,
                "base_kind": "architect",
                "acl_mode": "allow",
                "capabilities": {"deploy.apply": None},
            },
        )

    def test_canonical_acl_has_one_mode_and_one_rule_list(self):
        authority = compile_agent_class_acl(
            base_kind="architect",
            acl={
                "mode": "allow",
                "rules": [
                    {"capability": "message.engineer", "scope": "children"},
                    {"capability": "help.read"},
                ],
            },
            capabilities=CAPABILITY_CATALOG,
        )
        self.assertEqual(
            authority.as_snapshot()["capabilities"],
            {"help.read": None, "message.engineer": "children"},
        )

    def test_canonical_acl_rejects_legacy_parallel_rule_lists(self):
        invalid_acls = [
            {"mode": "allow", "allow": []},
            {"mode": "deny", "deny": []},
            {"mode": "allow", "rules": [], "deny": []},
            {"rules": []},
            {"mode": "allow"},
        ]
        for acl in invalid_acls:
            with self.subTest(acl=acl), self.assertRaises(
                AuthorityValidationError
            ):
                compile_agent_class_acl(
                    base_kind="architect",
                    acl=acl,
                    capabilities=CAPABILITY_CATALOG,
                )

    def test_effective_authority_snapshot_round_trip_and_validation(self):
        compiled = compile_agent_class_acl(
            base_kind="architect",
            acl={
                "mode": "allow",
                "rules": [
                    {"capability": "message.engineer", "scope": "children"},
                    {"capability": "help.read"},
                ],
            },
            capabilities=CAPABILITY_CATALOG,
        )
        restored = effective_authority_from_snapshot(
            compiled.as_snapshot(),
            capabilities=CAPABILITY_CATALOG,
        )
        self.assertEqual(restored, compiled)
        with self.assertRaises(AuthorityValidationError):
            effective_authority_from_snapshot(
                {
                    "base_kind": "worker",
                    "acl_mode": "allow",
                    "capabilities": {"engineer.hire": "children"},
                },
                capabilities=CAPABILITY_CATALOG,
            )
    def test_current_registered_surface_has_exact_authority_coverage(self):
        # Import after the pure primitive tests so an invalid production
        # registry fails at the same boundary used by the daemon.
        from torque.mcp import ALL_TOOLS, MCP_AUTHORITY_COVERAGE

        self.assertTrue(MCP_AUTHORITY_COVERAGE.ok)
        self.assertEqual(
            {tool["name"] for tool in ALL_TOOLS},
            set(MCP_TOOL_AUTHORITY_DEFINITIONS),
        )
        self.assertTrue(
            all(
                {
                    requirement.capability
                    for requirement in definition.requirements
                }.issubset(CAPABILITY_CATALOG)
                for definition in MCP_TOOL_AUTHORITY_DEFINITIONS.values()
            )
        )

    def test_tool_authority_is_colocated_validated_and_not_public(self):
        from torque.mcp import ALL_TOOLS, MCP_TOOL_AUTHORITY_DEFINITIONS
        from torque.mcp_tool_search import public_tool_spec

        task_show = next(
            tool for tool in ALL_TOOLS
            if tool["name"] == "architect_task_show"
        )
        definition = MCP_TOOL_AUTHORITY_DEFINITIONS["architect_task_show"]
        self.assertIn("authority", task_show)
        self.assertNotIn("authority", public_tool_spec(task_show))
        self.assertEqual(definition.requirements[0].capability, "task.read")
        self.assertEqual(definition.requirements[0].minimum_scope, "self")
        self.assertEqual(definition.requirements[0].target_argument, "task")
        self.assertEqual(definition.requirements[0].target_kind, "task")

    def test_tool_authority_rejects_missing_or_invalid_target_metadata(self):
        base = {
            "name": "example",
            "inputSchema": {
                "type": "object",
                "properties": {"agent_id": {"type": "string"}},
            },
        }
        with self.assertRaisesRegex(
            AuthorityValidationError, "requires authority metadata"
        ):
            authority_definition_from_tool_spec(
                base,
                base_kinds={"architect"},
                capabilities=CAPABILITY_CATALOG,
            )
        invalid = {
            **base,
            "authority": {
                "requirements": [{
                    "capability": "telemetry.read",
                    "minimum_scope": "children",
                    "target_argument": "missing",
                    "target_kind": "agent",
                }],
            },
        }
        with self.assertRaisesRegex(
            AuthorityValidationError, "is not in its schema"
        ):
            authority_definition_from_tool_spec(
                invalid,
                base_kinds={"architect"},
                capabilities=CAPABILITY_CATALOG,
            )

    def test_surface_authority_registries_are_individually_exact(self):
        from torque.mcp import MCP_AUTHORITY_SURFACE_COVERAGE

        self.assertEqual(
            set(MCP_AUTHORITY_SURFACE_COVERAGE),
            {"worker", "engineer", "architect"},
        )
        self.assertTrue(
            all(report.ok for report in MCP_AUTHORITY_SURFACE_COVERAGE.values())
        )

    def test_every_legacy_atom_translates_to_a_known_canonical_capability(self):
        self.assertTrue(set(LEGACY_ATOM_TO_CAPABILITY.values()).issubset(
            CAPABILITY_CATALOG
        ))
        self.assertTrue(set(CAPABILITIES).issubset(LEGACY_ATOM_TO_CAPABILITY))

    def test_legacy_profile_expansion_preserves_split_surface_permissions(self):
        expanded = canonical_capabilities_from_legacy_atoms({
            "observe.self_context",
            "profile.edit",
            "agent.manage_engineer_roster",
        })
        self.assertTrue({
            "self.read",
            "help.read",
            "tool.search",
            "behavior_overlay.propose",
            "behavior_overlay.admin",
            "specialization.read",
            "specialization.write",
        }.issubset(expanded))

    def test_current_tool_surface_has_complete_canonical_requirements(self):
        self.assertTrue(all(
            definition.requirements
            for definition in MCP_TOOL_AUTHORITY_DEFINITIONS.values()
        ))

    def test_projection_uses_each_tool_descriptor_minimum_scope(self):
        self_only = compile_agent_class_acl(
            base_kind="architect",
            acl={
                "mode": "allow",
                "rules": [{"capability": "task.read", "scope": "self"}],
            },
            capabilities=CAPABILITY_CATALOG,
        )
        group_read = compile_agent_class_acl(
            base_kind="architect",
            acl={
                "mode": "allow",
                "rules": [{"capability": "task.read", "scope": "group"}],
            },
            capabilities=CAPABILITY_CATALOG,
        )

        self.assertTrue(
            mcp_tool_allowed_by_authority("architect_task_list", self_only)
        )
        self.assertFalse(
            mcp_tool_allowed_by_authority("architect_task_chain", self_only)
        )
        self.assertTrue(
            mcp_tool_allowed_by_authority("architect_task_list", group_read)
        )
        self.assertTrue(
            mcp_tool_allowed_by_authority("architect_task_chain", group_read)
        )

    def test_projection_distinguishes_child_and_group_engineer_messages(self):
        children_only = compile_agent_class_acl(
            base_kind="engineer",
            acl={
                "mode": "allow",
                "rules": [
                    {"capability": "message.engineer", "scope": "children"}
                ],
            },
            capabilities=CAPABILITY_CATALOG,
        )
        group = compile_agent_class_acl(
            base_kind="engineer",
            acl={
                "mode": "allow",
                "rules": [
                    {"capability": "message.engineer", "scope": "group"}
                ],
            },
            capabilities=CAPABILITY_CATALOG,
        )

        self.assertFalse(
            mcp_tool_allowed_by_authority("engineer_peer_notify", children_only)
        )
        self.assertTrue(
            mcp_tool_allowed_by_authority("engineer_peer_notify", group)
        )

    def test_canonical_requirements_fit_each_registered_base_kind_surface(self):
        from torque.mcp import (
            ARCHITECT_TOOL_AUTHORITY_DEFINITIONS,
            ENGINEER_TOOL_AUTHORITY_DEFINITIONS,
            WORKER_TOOL_AUTHORITY_DEFINITIONS,
        )

        surfaces = (
            WORKER_TOOL_AUTHORITY_DEFINITIONS,
            ENGINEER_TOOL_AUTHORITY_DEFINITIONS,
            ARCHITECT_TOOL_AUTHORITY_DEFINITIONS,
        )
        for definitions in surfaces:
            for definition in definitions:
                for requirement in definition.requirements:
                    for base_kind in definition.base_kinds:
                        with self.subTest(
                            tool=definition.name,
                            capability=requirement.capability,
                            base_kind=base_kind,
                        ):
                            self.assertTrue(
                                CAPABILITY_CATALOG[
                                    requirement.capability
                                ].available_to(base_kind)
                            )

    def test_registry_hash_is_deterministic_and_versioned(self):
        first = registry_hash({"b": 2, "a": [1]})
        second = registry_hash({"a": [1], "b": 2})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
