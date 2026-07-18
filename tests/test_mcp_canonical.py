import json
from pathlib import Path
import re
import unittest
from types import SimpleNamespace

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub(include_json_helpers=True)

from torque.mcp import (
    ALL_TOOLS,
    _canonical_tools_for_caller,
    _resolve_public_tool_call,
)
from torque.mcp_canonical import canonical_tool_name
from torque.mcp_tool_search import public_tool_spec, tool_search_response


class _State:
    def __init__(self, kind, *, authority=None, hired=True):
        snapshot = {}
        if authority is not None:
            snapshot = {"effective_authority": authority}
        self.agents = {
            "caller": SimpleNamespace(
                id="caller",
                name="Caller",
                group="g",
                kind=kind,
                hired_by_architect_id=(
                    "supervisor" if kind == "engineer" and hired else ""
                ),
                effective_agent_class_snapshot=snapshot,
            ),
        }

    @staticmethod
    def agent_is_tombstoned(_cell):
        return False

    @staticmethod
    def get_engineer_settings(_group):
        return SimpleNamespace(engineer_can_override_worker_provider=True)


class CanonicalMCPContractTests(unittest.TestCase):
    @staticmethod
    def _legacy_tool_names():
        raw_names = {tool["name"] for tool in ALL_TOOLS}
        canonical_names = {canonical_tool_name(name) for name in raw_names}
        return raw_names - canonical_names

    def _assert_routes_to_schema(
        self,
        kind,
        canonical_name,
        arguments,
        expected_legacy_name,
    ):
        resolved, translated = _resolve_public_tool_call(
            _State(kind),
            "caller",
            canonical_name,
            arguments,
        )
        self.assertEqual(resolved, expected_legacy_name)
        legacy_spec = next(
            tool for tool in ALL_TOOLS
            if tool["name"] == expected_legacy_name
        )
        schema = legacy_spec.get("inputSchema", {})
        properties = set((schema.get("properties") or {}).keys())
        self.assertFalse(
            set(translated) - properties,
            f"{canonical_name} passed unsupported arguments to "
            f"{expected_legacy_name}: {set(translated) - properties}",
        )
        self.assertFalse(
            set(schema.get("required") or []) - set(translated),
            f"{canonical_name} omitted required arguments for "
            f"{expected_legacy_name}: "
            f"{set(schema.get('required') or []) - set(translated)}",
        )

    def test_role_and_proposal_variants_share_one_public_name(self):
        aliases = {
            "architect_peer_message",
            "engineer_peer_notify",
            "architect_proposal_peer_message",
        }
        self.assertEqual(
            {canonical_tool_name(name) for name in aliases},
            {"peer_message"},
        )

    def test_default_surfaces_are_unique_canonical_and_bounded(self):
        limits = {
            "worker": (24, 24),
            "engineer": (30, 75),
            "architect": (30, 100),
        }
        legacy_names = {tool["name"] for tool in ALL_TOOLS}
        for kind, (eager_limit, total_limit) in limits.items():
            with self.subTest(kind=kind):
                tools = _canonical_tools_for_caller(_State(kind), "caller")
                names = [tool["name"] for tool in tools]
                eager = [
                    tool for tool in tools
                    if not tool.get("deferred")
                ]
                self.assertEqual(len(names), len(set(names)))
                self.assertLessEqual(len(eager), eager_limit)
                self.assertLessEqual(len(tools), total_limit)
                self.assertFalse(set(names) & legacy_names)

    def test_architect_boot_reads_are_eager(self):
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect"),
                "caller",
            )
        }

        self.assertFalse(tools["context"].get("deferred"))
        self.assertFalse(tools["event_list"].get("deferred"))
        self.assertTrue(tools["task_mark_covered"].get("deferred"))

    def test_architect_worktree_tools_are_deferred_and_route_canonically(self):
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect"),
                "caller",
            )
        }

        for name in (
            "worktree_merge",
            "worktree_rebase",
            "worktree_create_pr",
            "worktree_diff",
        ):
            self.assertIn(name, tools)
            self.assertTrue(tools[name].get("deferred"), name)
            self.assertEqual(
                tools[name]["inputSchema"].get("required"),
                ["agent"],
            )
            self.assertNotIn(
                "worktree_path",
                tools[name]["inputSchema"]["properties"],
            )

        resolved, translated = _resolve_public_tool_call(
            _State("architect"),
            "caller",
            "worktree_merge",
            {"agent": "courier", "pr_title": "Land reviewed change"},
        )
        self.assertEqual(resolved, "architect_merge")
        self.assertEqual(
            translated,
            {"agent": "courier", "pr_title": "Land reviewed change"},
        )

    def test_architect_worktree_projection_obeys_restricted_authority(self):
        merge_authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {
                "self.read": "self",
                "worktree.merge": "children",
            },
        }
        merge_state = _State("architect", authority=merge_authority)
        merge_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(merge_state, "caller")
        }

        self.assertIn("worktree_merge", merge_tools)
        self.assertIn("worktree_rebase", merge_tools)
        self.assertIn("worktree_create_pr", merge_tools)
        self.assertNotIn("worktree_diff", merge_tools)
        search = tool_search_response(
            merge_tools.values(),
            {"query": "select:worktree_merge"},
        )
        self.assertIn('"name": "worktree_merge"', search)

        read_authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {
                "self.read": "self",
                "worktree.read": "children",
            },
        }
        read_tools = {
            tool["name"]
            for tool in _canonical_tools_for_caller(
                _State("architect", authority=read_authority),
                "caller",
            )
        }
        self.assertIn("worktree_diff", read_tools)
        self.assertNotIn("worktree_merge", read_tools)

    def test_public_schemas_never_advertise_legacy_tool_names(self):
        legacy_names = self._legacy_tool_names()
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])("
            + "|".join(
                re.escape(name)
                for name in sorted(legacy_names, key=len, reverse=True)
            )
            + r")(?![A-Za-z0-9_])"
        )
        for kind in ("worker", "engineer", "architect"):
            for tool in _canonical_tools_for_caller(_State(kind), "caller"):
                with self.subTest(kind=kind, tool=tool["name"]):
                    serialized = json.dumps(
                        public_tool_spec(tool),
                        sort_keys=True,
                    )
                    self.assertIsNone(pattern.search(serialized))

    def test_canonical_schemas_remove_argument_aliases_and_keep_constraints(self):
        engineer_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("engineer"),
                "caller",
            )
        }
        artifact_schema = engineer_tools["task_artifact_upload"]["inputSchema"]
        self.assertIn("task", artifact_schema["required"])
        self.assertNotIn("task_id", artifact_schema["properties"])

        dispatch_schema = engineer_tools["task_dispatch"]["inputSchema"]
        self.assertNotIn("agent_type", dispatch_schema["properties"])
        self.assertEqual(len(dispatch_schema["oneOf"]), 2)

        architect_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect"),
                "caller",
            )
        }
        covered_properties = architect_tools[
            "task_mark_covered"
        ]["inputSchema"]["properties"]
        self.assertNotIn("covering_task_id", covered_properties)

    def test_maintained_prompts_and_docs_use_only_canonical_tool_names(self):
        legacy_names = self._legacy_tool_names()
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])("
            + "|".join(
                re.escape(name)
                for name in sorted(legacy_names, key=len, reverse=True)
            )
            + r")(?![A-Za-z0-9_])"
        )
        repo_root = Path(__file__).resolve().parents[1]
        docs_root = repo_root / "docs"
        paths = [
            path
            for path in docs_root.rglob("*.md")
            if "plans" not in path.parts
        ]
        paths.extend([repo_root / "AGENTS.md", repo_root / "CLAUDE.md"])
        for relative_root in (
            "actions",
            "skills",
            ".claude/skills",
            "torque/builtin_agent_classes",
        ):
            root = repo_root / relative_root
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
            )
        hits = []
        for path in paths:
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                1,
            ):
                for match in pattern.finditer(line):
                    hits.append(
                        f"{path.relative_to(repo_root)}:"
                        f"{line_number}: {match.group(1)}"
                    )
                for legacy_name in legacy_names:
                    qualified = "mcp__torque__" + legacy_name
                    if qualified in line:
                        hits.append(
                            f"{path.relative_to(repo_root)}:"
                            f"{line_number}: {qualified}"
                        )
        self.assertEqual(hits, [])

    def test_reference_covers_every_canonical_operation(self):
        reference = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "reference"
            / "mcp-tools.md"
        ).read_text(encoding="utf-8")
        canonical_names = {
            canonical_tool_name(tool["name"])
            for tool in ALL_TOOLS
        }
        missing = sorted(
            name for name in canonical_names
            if f"`{name}`" not in reference
        )
        self.assertEqual(missing, [])

    def test_peer_message_resolves_by_caller_relationship(self):
        engineer_name, engineer_args = _resolve_public_tool_call(
            _State("engineer"),
            "caller",
            "peer_message",
            {
                "peer": "engineer-2",
                "message": "Please inspect this task.",
                "context_task_ids": ["task-1"],
            },
        )
        self.assertEqual(engineer_name, "engineer_peer_notify")
        self.assertEqual(engineer_args["engineer_id"], "engineer-2")
        self.assertNotIn("peer", engineer_args)

        architect_name, architect_args = _resolve_public_tool_call(
            _State("architect"),
            "caller",
            "peer_message",
            {"peer": "architect-2", "message": "Coordinate scope."},
        )
        self.assertEqual(architect_name, "architect_peer_message")
        self.assertEqual(architect_args["architect_id"], "architect-2")
        self.assertNotIn("peer", architect_args)

    def test_consolidated_dispatch_selects_single_or_batch_handler(self):
        state = _State("engineer")
        single_name, single_args = _resolve_public_tool_call(
            state,
            "caller",
            "task_dispatch",
            {"task": "task-1"},
        )
        self.assertEqual(single_name, "engineer_task_dispatch")
        self.assertEqual(single_args["task"], "task-1")

        batch_name, batch_args = _resolve_public_tool_call(
            state,
            "caller",
            "task_dispatch",
            {"entries": [{"task": "task-1"}, {"task": "task-2"}]},
        )
        self.assertEqual(batch_name, "engineer_batch_dispatch")
        self.assertEqual(
            batch_args["tasks"],
            [{"task": "task-1"}, {"task": "task-2"}],
        )
        self.assertNotIn("entries", batch_args)

    def test_proposal_only_authority_cannot_select_executable_handlers(self):
        authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {
                "task.propose": "self",
                "decision.propose": "self",
            },
        }
        state = _State("architect", authority=authority)

        task_name, _ = _resolve_public_tool_call(
            state,
            "caller",
            "task_create",
            {"title": "Proposed task"},
        )
        decision_name, _ = _resolve_public_tool_call(
            state,
            "caller",
            "decision_create",
            {"title": "Proposed decision", "rationale": "Explore"},
        )
        self.assertEqual(task_name, "architect_task_propose")
        self.assertEqual(decision_name, "architect_decision_propose")

        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(state, "caller")
        }
        task_properties = set(
            tools["task_create"]["inputSchema"]["properties"]
        )
        self.assertNotIn("assigned_engineer_id", task_properties)
        self.assertNotIn("dispatch", task_properties)

        full_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect"),
                "caller",
            )
        }
        full_message_properties = set(
            full_tools["user_message"]["inputSchema"]["properties"]
        )
        self.assertNotIn("context_area_ids", full_message_properties)

    def test_user_owned_engineer_has_no_supervisor_channel(self):
        names = {
            tool["name"]
            for tool in _canonical_tools_for_caller(
                _State("engineer", hired=False),
                "caller",
            )
        }
        self.assertNotIn("supervisor_message", names)
        self.assertNotIn("agent_reply", names)

    def test_hidden_legacy_aliases_cannot_escape_the_caller_catalog(self):
        for kind, legacy_name in (
            ("worker", "engineer_task_create"),
            ("engineer", "torque_done"),
            ("architect", "torque_done"),
        ):
            with self.subTest(kind=kind, legacy_name=legacy_name):
                resolved, _ = _resolve_public_tool_call(
                    _State(kind),
                    "caller",
                    legacy_name,
                    {},
                )
                self.assertEqual(resolved, "")

        resolved, arguments = _resolve_public_tool_call(
            _State("engineer"),
            "caller",
            "engineer_task_dispatch",
            {"task": "task-1"},
        )
        self.assertEqual(resolved, "engineer_task_dispatch")
        self.assertEqual(arguments, {"task": "task-1"})

    def test_proposal_only_legacy_alias_cannot_bypass_projected_authority(self):
        authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {
                "task.propose": "self",
            },
        }
        resolved, _ = _resolve_public_tool_call(
            _State("architect", authority=authority),
            "caller",
            "architect_task_create",
            {"title": "Executable task"},
        )
        self.assertEqual(resolved, "")

    def test_every_consolidated_selector_routes_to_a_compatible_schema(self):
        cases = [
            ("worker", "memory_set_pin", {"entry_id": "m1", "pinned": True}, "torque_memory_pin"),
            ("worker", "memory_set_pin", {"entry_id": "m1", "pinned": False}, "torque_memory_unpin"),
            (
                "engineer",
                "help_search",
                {"audience": "engineer"},
                "engineer_help_list",
            ),
            (
                "engineer",
                "help_search",
                {"query": "tasks", "limit": 3},
                "engineer_help_search",
            ),
            ("engineer", "help_query", {"question": "How?"}, "engineer_help_query"),
            ("engineer", "task_dispatch", {"task": "t1"}, "engineer_task_dispatch"),
            (
                "engineer",
                "task_dispatch",
                {"entries": [{"task": "t1"}]},
                "engineer_batch_dispatch",
            ),
            (
                "engineer",
                "event_delivery_update",
                {"operation": "configure", "preset": "quiet"},
                "engineer_notifications",
            ),
            (
                "engineer",
                "event_delivery_update",
                {"operation": "resume"},
                "engineer_resume",
            ),
            (
                "architect",
                "behavior_overlay_propose",
                {"target_kind": "self", "text": "x", "rationale": "y"},
                "architect_behavior_overlay_propose",
            ),
            (
                "architect",
                "behavior_overlay_propose",
                {"target_kind": "agent", "target": "e1", "text": "x", "rationale": "y"},
                "architect_behavior_overlay_propose_for_engineer",
            ),
            (
                "architect",
                "behavior_overlay_propose",
                {"target_kind": "role", "target": "engineer", "text": "x", "rationale": "y"},
                "architect_behavior_overlay_propose_for_role",
            ),
            (
                "architect",
                "behavior_overlay_review",
                {"proposal_id": "p1", "decision": "approve"},
                "architect_behavior_overlay_approve",
            ),
            (
                "architect",
                "behavior_overlay_review",
                {
                    "proposal_id": "p1",
                    "decision": "reject",
                    "expected_proposed_text_sha256": "abc",
                },
                "architect_behavior_overlay_reject",
            ),
            (
                "architect",
                "behavior_overlay_rollback",
                {
                    "target_kind": "self",
                    "version_id": "v1",
                    "rationale": "undo",
                },
                "architect_behavior_overlay_rollback",
            ),
            (
                "architect",
                "behavior_overlay_rollback",
                {
                    "target_kind": "role",
                    "target": "engineer",
                    "version_id": "v1",
                    "rationale": "undo",
                },
                "architect_behavior_overlay_rollback_role",
            ),
            (
                "architect",
                "event_delivery_update",
                {"operation": "configure", "set": ["task_completed"]},
                "architect_digest_filter",
            ),
            (
                "architect",
                "engineer_lifecycle",
                {"engineer_id": "e1", "operation": "dismiss", "reason": "done"},
                "architect_engineer_dismiss",
            ),
            (
                "architect",
                "engineer_lifecycle",
                {"engineer_id": "e1", "operation": "rehire"},
                "architect_engineer_rehire",
            ),
            (
                "architect",
                "engineer_lifecycle",
                {"engineer_id": "e1", "operation": "restore"},
                "architect_engineer_restore",
            ),
            ("architect", "hire_list", {}, "architect_pending_hire_list"),
            (
                "architect",
                "hire_list",
                {"hire_id": "h1"},
                "architect_pending_hire_status",
            ),
        ]
        for target_kind in ("task", "decision", "initiative", "area"):
            for operation in ("add", "remove"):
                suffix = "link" if operation == "add" else "unlink"
                cases.append((
                    "architect",
                    "area_link",
                    {
                        "area": "a1",
                        "operation": operation,
                        "target_kind": target_kind,
                        "target": "x1",
                        "relation": "related",
                    },
                    f"architect_area_{suffix}_{target_kind}",
                ))
        for target_kind in ("task", "decision"):
            for operation in ("add", "remove"):
                suffix = "link" if operation == "add" else "unlink"
                cases.append((
                    "architect",
                    "initiative_link",
                    {
                        "initiative": "i1",
                        "operation": operation,
                        "target_kind": target_kind,
                        "target": "x1",
                    },
                    f"architect_initiative_{suffix}_{target_kind}",
                ))
        for operation in ("create", "update", "archive"):
            arguments = {"area": "a1", "operation": operation}
            if operation == "create":
                arguments.update({"note_type": "note", "title": "Title"})
            else:
                arguments["note"] = "n1"
            cases.append((
                "architect",
                "area_note",
                arguments,
                f"architect_area_note_{operation}",
            ))
        for transition in ("propose", "park", "archive"):
            cases.append((
                "architect",
                "idea_brief_transition",
                {"idea_brief": "b1", "transition": transition},
                f"architect_idea_brief_{transition}",
            ))
        cases.extend([
            (
                "architect",
                "idea_brief_update",
                {"idea_brief": "b1", "operation": "update", "title": "Title"},
                "architect_idea_brief_update",
            ),
            (
                "architect",
                "idea_brief_update",
                {
                    "idea_brief": "b1",
                    "operation": "refine",
                    "refinement_note": "More detail",
                },
                "architect_idea_brief_refine",
            ),
            (
                "architect",
                "thinking_list",
                {"artifact_type": "scratchpad"},
                "architect_thinking_scratchpad_list",
            ),
            (
                "architect",
                "thinking_list",
                {"artifact_type": "mind_map"},
                "architect_thinking_mind_map_list",
            ),
            (
                "architect",
                "thinking_get",
                {"artifact_type": "scratchpad", "artifact": "s1"},
                "architect_thinking_scratchpad_show",
            ),
            (
                "architect",
                "thinking_get",
                {"artifact_type": "mind_map", "artifact": "m1"},
                "architect_thinking_mind_map_show",
            ),
            (
                "architect",
                "scratchpad_update",
                {"operation": "create", "title": "Scratch"},
                "architect_thinking_scratchpad_create",
            ),
            (
                "architect",
                "scratchpad_update",
                {"operation": "update", "scratchpad": "s1", "title": "Scratch"},
                "architect_thinking_scratchpad_update",
            ),
            (
                "architect",
                "mind_map_update",
                {"operation": "create", "title": "Map"},
                "architect_thinking_mind_map_create",
            ),
            (
                "architect",
                "mind_map_update",
                {"operation": "update", "mind_map": "m1", "title": "Map"},
                "architect_thinking_mind_map_update",
            ),
            (
                "architect",
                "mind_map_node_update",
                {"operation": "create", "mind_map": "m1", "label": "Node"},
                "architect_thinking_mind_map_node_create",
            ),
            (
                "architect",
                "mind_map_node_update",
                {
                    "operation": "update",
                    "mind_map": "m1",
                    "node": "n1",
                    "label": "Node",
                },
                "architect_thinking_mind_map_node_update",
            ),
            (
                "architect",
                "mind_map_node_update",
                {
                    "operation": "move",
                    "mind_map": "m1",
                    "node": "n1",
                    "x": 10,
                },
                "architect_thinking_mind_map_node_position",
            ),
            (
                "architect",
                "mind_map_node_update",
                {"operation": "delete", "mind_map": "m1", "node": "n1"},
                "architect_thinking_mind_map_node_delete",
            ),
            (
                "architect",
                "mind_map_link_update",
                {
                    "operation": "create",
                    "mind_map": "m1",
                    "source": "n1",
                    "target": "n2",
                },
                "architect_thinking_mind_map_link_create",
            ),
            (
                "architect",
                "mind_map_link_update",
                {
                    "operation": "update",
                    "mind_map": "m1",
                    "link": "l1",
                    "label": "depends on",
                },
                "architect_thinking_mind_map_link_update",
            ),
            (
                "architect",
                "mind_map_link_update",
                {"operation": "delete", "mind_map": "m1", "link": "l1"},
                "architect_thinking_mind_map_link_delete",
            ),
        ])

        for case in cases:
            with self.subTest(canonical=case[1], expected=case[3]):
                self._assert_routes_to_schema(*case)


if __name__ == "__main__":
    unittest.main()
