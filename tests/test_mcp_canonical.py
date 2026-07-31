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
    CANONICAL_CALLABLE_HANDLER_REGISTRY,
    _canonical_tools_for_caller,
    _resolve_public_tool_call,
    _visible_tools,
)
from torque.mcp_canonical import (
    ARCHITECT_EAGER_TOOL_NAMES,
    ENGINEER_EAGER_TOOL_NAMES,
    canonical_registry_missing_handlers,
    canonical_tool_name,
)
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

    def test_mind_map_tools_are_not_projected_for_any_caller_kind(self):
        removed = {"mind_map_update", "mind_map_node_update", "mind_map_link_update"}
        for kind in ("worker", "engineer", "architect"):
            with self.subTest(kind=kind):
                projected = {tool["name"] for tool in _canonical_tools_for_caller(_State(kind), "caller")}
                self.assertFalse(projected & removed)

    def test_raise_is_the_only_public_blocking_escalation_name(self):
        expected_handlers = {
            "worker": "torque_ask",
            "engineer": "engineer_ask",
            "architect": "architect_ask",
        }
        for kind, handler in expected_handlers.items():
            with self.subTest(kind=kind):
                tools = _canonical_tools_for_caller(_State(kind), "caller")
                names = {tool["name"] for tool in tools}
                self.assertIn("raise", names)
                self.assertNotIn("user_ask", names)
                self.assertIn("raise", {
                    tool["name"] for tool in _visible_tools(_State(kind), "caller")
                })
                spec = next(tool for tool in tools if tool["name"] == "raise")
                self.assertIn("immediate decision owner", spec["description"])
                self.assertIn("question", spec["inputSchema"]["required"])
                self.assertEqual(
                    _resolve_public_tool_call(
                        _State(kind), "caller", "raise", {"question": "Proceed?"}
                    )[0],
                    handler,
                )
                # Previous canonical name is an unadvertised migration alias.
                self.assertEqual(
                    _resolve_public_tool_call(
                        _State(kind), "caller", "user_ask", {"question": "Proceed?"}
                    )[0],
                    handler,
                )
                # ``raise`` is eager, so deferred search need not return it;
                # it must never resurrect the retired public name.
                self.assertNotIn(
                    "user_ask", tool_search_response(tools, {"query": "raise"})
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
            "worker": (27, 27),
            "engineer": (59, 77),
            "architect": (57, 105),
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
                self.assertEqual(tools[:len(eager)], eager)
                self.assertTrue(all(
                    tool.get("deferred")
                    for tool in tools[len(eager):]
                ))
                self.assertFalse(set(names) & legacy_names)

    def test_action_catalog_is_callable_for_every_agent_kind(self):
        # Broad read availability is deliberate: task creators need this to
        # choose suggested_action, and workers need it to inspect legal derive
        # exits. It remains read-only and scoped to the caller's group.
        for kind in ("worker", "engineer", "architect"):
            with self.subTest(kind=kind):
                tools = {
                    tool["name"]: tool
                    for tool in _canonical_tools_for_caller(_State(kind), "caller")
                }
                self.assertIn("action_list", tools)
                self.assertIn(
                    "transitions", tools["action_list"]["description"],
                )
                self.assertIn(
                    "action_list",
                    {tool["name"] for tool in _visible_tools(_State(kind), "caller")},
                )
                resolved, translated = _resolve_public_tool_call(
                    _State(kind), "caller", "action_list", {"group": "g"},
                )
                self.assertIn(
                    resolved,
                    CANONICAL_CALLABLE_HANDLER_REGISTRY["action_list"],
                )
                self.assertEqual(translated, {"group": "g"})

    def test_architect_advertised_catalog_has_callable_registered_handlers(self):
        """Eager/deferred public names must share the runtime handler registry."""

        tools = _canonical_tools_for_caller(_State("architect"), "caller")
        self.assertEqual(
            (),
            canonical_registry_missing_handlers(
                tools,
                CANONICAL_CALLABLE_HANDLER_REGISTRY,
            ),
        )

        eager = {tool["name"] for tool in tools if not tool.get("deferred")}
        deferred = {tool["name"] for tool in tools if tool.get("deferred")}
        for public_name, expected_handler in {
            "task_claim": "architect_task_pickup",
            "task_mark_covered": "architect_task_mark_covered",
            "task_coverage_reconcile": "architect_task_coverage_reconcile",
        }.items():
            with self.subTest(public_name=public_name):
                self.assertIn(public_name, eager)
                self.assertNotIn(public_name, deferred)
                self.assertIn(
                    expected_handler,
                    CANONICAL_CALLABLE_HANDLER_REGISTRY[public_name],
                )
                resolved, _translated = _resolve_public_tool_call(
                    _State("architect"),
                    "caller",
                    public_name,
                    {"task": "TORQUE:fixture"},
                )
                self.assertEqual(expected_handler, resolved)

    def test_architect_agent_message_schema_requires_explicit_task_dispatch(self):
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(_State("architect"), "caller")
        }

        task_description = tools["agent_message"]["inputSchema"]["properties"][
            "task"
        ]["description"]
        self.assertIn("Only this explicit argument marks a task live", task_description)
        self.assertIn("when omitted, the message marks no task live", task_description)
        self.assertIn("mentions task IDs or slugs", task_description)

    def test_coverage_reconcile_discovery_and_selection_are_truthful(self):
        """Discovery presents the provisional route before a planner calls it."""
        docs = Path("docs/reference/mcp-tools.md").read_text(encoding="utf-8")
        self.assertIn("task_coverage_reconcile", docs)
        self.assertIn("TORQUE:1228 is merged and the caller session is relaunched", docs)

        for kind, expected_handler in (
            ("architect", "architect_task_coverage_reconcile"),
            ("engineer", "engineer_task_coverage_reconcile"),
        ):
            with self.subTest(kind=kind):
                specs = {
                    tool["name"]: tool
                    for tool in _canonical_tools_for_caller(_State(kind), "caller")
                }
                spec = specs["task_coverage_reconcile"]
                self.assertFalse(spec.get("deferred"))
                # This human-readable description is the initial planning
                # surface, rather than hidden activation metadata.
                for fragment in (
                    "NOT YET AVAILABLE",
                    "TORQUE:1228 is merged",
                    "session is relaunched",
                ):
                    self.assertIn(fragment, spec["description"])
                self._assert_routes_to_schema(
                    kind,
                    "task_coverage_reconcile",
                    {"task_ids": ["TORQUE:fixture"]},
                    expected_handler,
                )

        # The historical hygiene operation remains a separate public route
        # with its established inventory/apply/limit shape.
        self.assertEqual(
            canonical_tool_name("architect_proposal_root_backlog_hygiene"),
            "proposal_root_backlog_hygiene",
        )
        self._assert_routes_to_schema(
            "architect",
            "proposal_root_backlog_hygiene",
            {"apply": False, "task_ids": ["TORQUE:fixture"], "limit": 1},
            "architect_proposal_root_backlog_hygiene",
        )

    def test_architect_core_orchestration_categories_are_eager(self):
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect"),
                "caller",
            )
        }
        eager = {
            name
            for name, tool in tools.items()
            if not tool.get("deferred")
        }

        self.assertEqual(eager, ARCHITECT_EAGER_TOOL_NAMES)
        for category in (
            {"peer_message", "agent_message", "user_message", "agent_reply"},
            {"task_create", "task_get", "task_update", "task_reassign"},
            {"journal_list", "decision_get", "memory_get", "semantic_recall"},
            {"wave_summary", "task_chain", "task_verify", "worktree_merge"},
        ):
            self.assertTrue(category <= eager)
        self.assertTrue(tools["engineer_hire"].get("deferred"))
        self.assertTrue(tools["thinking_list"].get("deferred"))

        resolved, translated = _resolve_public_tool_call(
            _State("architect"),
            "caller",
            "task_verify",
            {"task": "TORQUE:1", "tests_run": "focused suite"},
        )
        self.assertEqual(resolved, "architect_task_verify")
        self.assertEqual(
            translated,
            {"task": "TORQUE:1", "tests_run": "focused suite"},
        )

    def test_architect_worktree_tools_are_eager_and_route_canonically(self):
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
            self.assertFalse(tools[name].get("deferred"), name)
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
        self.assertFalse(merge_tools["worktree_merge"].get("deferred"))

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

    def test_architect_loop_tools_require_message_user_authority(self):
        allowed_authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {
                "self.read": "self",
                "message.user": "self",
            },
        }
        allowed_state = _State("architect", authority=allowed_authority)
        allowed_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(allowed_state, "caller")
        }
        self.assertIn("user_message_loop_get", allowed_tools)
        self.assertIn("user_message_loop_stop", allowed_tools)
        self.assertFalse(allowed_tools["user_message_loop_get"].get("deferred"))
        self.assertFalse(allowed_tools["user_message_loop_stop"].get("deferred"))
        self.assertEqual(
            _resolve_public_tool_call(
                allowed_state, "caller", "user_message_loop_get", {},
            )[0],
            "torque_get_user_message_loop",
        )
        self.assertEqual(
            _resolve_public_tool_call(
                allowed_state, "caller", "user_message_loop_stop",
                {"reason": "complete"},
            )[0],
            "torque_stop_user_message_loop",
        )

        denied_authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {"self.read": "self"},
        }
        denied_tools = {
            tool["name"]
            for tool in _canonical_tools_for_caller(
                _State("architect", authority=denied_authority), "caller",
            )
        }
        self.assertNotIn("user_message_loop_get", denied_tools)
        self.assertNotIn("user_message_loop_stop", denied_tools)

    def test_restricted_architect_denies_core_tools_without_capabilities(self):
        restricted_authority = {
            "schema_version": 1,
            "base_kind": "architect",
            "acl_mode": "allow",
            "capabilities": {"self.read": "self"},
        }
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("architect", authority=restricted_authority),
                "caller",
            )
        }
        self.assertEqual(
            set(tools), {"context", "tool_search", "action_list"},
        )
        for denied in (
            "peer_message",
            "task_create",
            "memory_get",
            "semantic_recall",
            "wave_summary",
            "task_verify",
            "worktree_merge",
        ):
            self.assertNotIn(denied, tools)
            self.assertNotIn(
                f'"name": "{denied}"',
                tool_search_response(
                    tools.values(),
                    {"query": f"select:{denied}"},
                ),
            )

    def test_engineer_execution_categories_are_eager_and_route_canonically(self):
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("engineer"),
                "caller",
            )
        }
        eager = {
            name
            for name, tool in tools.items()
            if not tool.get("deferred")
        }
        categories = (
            {
                "memory_publish", "memory_list", "memory_get",
                "memory_set_pin", "memory_link", "semantic_recall",
                "journal_write", "journal_list",
            },
            {
                "stream_list", "stream_get", "action_list", "action_get",
                "hint_set_state", "event_delivery_update",
            },
            {
                "worktree_diff", "worktree_checkpoint",
                "worktree_advance_boundary", "worktree_rebase",
                "worktree_create_pr", "worktree_merge", "worktree_remove",
                "worktree_adopt",
            },
            {
                "task_mark_covered", "task_coverage_reconcile",
                "task_reassign", "agent_launch_settings", "agent_close",
                "agent_relaunch",
            },
        )

        self.assertEqual(len(eager), 59)
        self.assertEqual(eager, ENGINEER_EAGER_TOOL_NAMES)
        initial_projection = _visible_tools(_State("engineer"), "caller")
        self.assertEqual(
            {tool["name"] for tool in initial_projection[:59]},
            ENGINEER_EAGER_TOOL_NAMES,
        )
        for category in categories:
            self.assertTrue(category <= eager)
            for name in category:
                self.assertIn(name, tools)
                self.assertFalse(tools[name].get("deferred"), name)
                self.assertNotIn(
                    f'"name": "{name}"',
                    tool_search_response(
                        tools.values(),
                        {"query": f"select:{name}"},
                    ),
                    name,
                )

        for name, arguments, legacy_name in (
            ("memory_get", {"entry_id": "abc"}, "torque_memory_read"),
            ("stream_get", {"stream": "stream-1"}, "engineer_stream_show"),
            (
                "action_get",
                {"name": "feature/implement"},
                "engineer_action_show",
            ),
            (
                "worktree_checkpoint",
                {"agent": "courier"},
                "engineer_worktree_checkpoint",
            ),
            (
                "task_reassign",
                {"task": "TORQUE:1", "new_engineer_id": "engineer-2"},
                "engineer_task_reassign",
            ),
            (
                "agent_relaunch",
                {"agent": "courier"},
                "engineer_agent_relaunch",
            ),
        ):
            self._assert_routes_to_schema(
                "engineer",
                name,
                arguments,
                legacy_name,
            )

    def test_restricted_engineer_denies_eager_tools_and_exact_search(self):
        worktree_authority = {
            "schema_version": 1,
            "base_kind": "engineer",
            "acl_mode": "allow",
            "capabilities": {
                "self.read": "self",
                "worktree.merge": "children",
            },
        }
        worktree_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("engineer", authority=worktree_authority),
                "caller",
            )
        }
        for name in (
            "worktree_merge",
            "worktree_rebase",
            "worktree_create_pr",
            "worktree_checkpoint",
            "worktree_advance_boundary",
            "worktree_remove",
            "worktree_adopt",
        ):
            self.assertIn(name, worktree_tools)
            self.assertFalse(worktree_tools[name].get("deferred"), name)
        self.assertNotIn("worktree_diff", worktree_tools)

        restricted_authority = {
            "schema_version": 1,
            "base_kind": "engineer",
            "acl_mode": "allow",
            "capabilities": {
                "self.read": "self",
            },
        }
        restricted_tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(
                _State("engineer", authority=restricted_authority),
                "caller",
            )
        }
        self.assertEqual(
            set(restricted_tools), {"context", "tool_search", "action_list"},
        )
        for denied in (
            "memory_get",
            "semantic_recall",
            "stream_get",
            "action_get",
            "worktree_merge",
            "worktree_checkpoint",
            "task_reassign",
            "agent_relaunch",
        ):
            self.assertNotIn(denied, restricted_tools)
            self.assertNotIn(
                f'"name": "{denied}"',
                tool_search_response(
                    restricted_tools.values(),
                    {"query": f"select:{denied}"},
                ),
            )

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

    def test_depth_zero_combinator_inventory_is_exact_per_kind(self):
        """Lock the emitted depth-zero combinator set for every caller kind.

        ``behavior_overlay_propose`` must be absent from every set. The
        nested ``task_list`` combinator remains the negative control.
        """
        expected_depth_zero = {
            "worker": {"help_search"},
            "engineer": {
                "event_delivery_update",
                "help_search",
                "task_dispatch",
            },
            "architect": {
                "area_note",
                "behavior_overlay_rollback",
                "engineer_lifecycle",
                "help_search",
                "hire_list",
                "idea_brief_update",
                "scratchpad_update",
                "task_dispatch",
            },
        }
        combinators = {"oneOf", "anyOf", "allOf", "not"}
        for kind, expected in expected_depth_zero.items():
            with self.subTest(kind=kind):
                tools = {
                    tool["name"]: tool["inputSchema"]
                    for tool in _canonical_tools_for_caller(_State(kind), "caller")
                }
                actual = {
                    name
                    for name, schema in tools.items()
                    if combinators & set(schema)
                }
                self.assertEqual(actual, expected)
                self.assertNotIn("behavior_overlay_propose", actual)

        task_list_schema = {
            tool["name"]: tool["inputSchema"]
            for tool in _canonical_tools_for_caller(_State("architect"), "caller")
        }["task_list"]
        self.assertFalse(combinators & set(task_list_schema))
        self.assertIn(
            "oneOf",
            task_list_schema["properties"]["label_filter"],
        )

    def test_behavior_overlay_propose_schema_is_plain_object(self):
        tools = {
            tool["name"]: tool["inputSchema"]
            for tool in _canonical_tools_for_caller(_State("architect"), "caller")
        }
        schema = tools["behavior_overlay_propose"]
        self.assertEqual(schema["type"], "object")
        self.assertTrue({"target_kind", "target"} <= set(schema["properties"]))
        self.assertIn("target_kind", schema["required"])
        self.assertFalse({"oneOf", "anyOf", "allOf", "not"} & set(schema))

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

    def test_architect_agent_references_use_one_public_spelling(self):
        """Read/telemetry neighbors must not make ``agent_id`` the guess."""
        tools = {
            tool["name"]: tool
            for tool in _canonical_tools_for_caller(_State("architect"), "caller")
        }
        cases = {
            "behavior_overlay_get": "architect_behavior_overlay_read",
            "behavior_overlay_versions": "architect_behavior_overlay_versions",
            "behavior_overlay_diff": "architect_behavior_overlay_diff",
            "behavior_overlay_proposal_list": (
                "architect_behavior_overlay_proposal_list"
            ),
            "telemetry_query": "architect_mcp_calls",
        }
        for public_name, legacy_name in cases.items():
            with self.subTest(public_name=public_name):
                properties = tools[public_name]["inputSchema"]["properties"]
                self.assertIn("agent", properties)
                self.assertNotIn("agent_id", properties)
                self.assertNotIn("cell_id", properties)
                resolved, translated = _resolve_public_tool_call(
                    _State("architect"),
                    "caller",
                    public_name,
                    {"agent": "engineer-1"},
                )
                self.assertEqual(resolved, legacy_name)
                self.assertEqual(translated["agent_id"], "engineer-1")
                self.assertNotIn("agent", translated)

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
                "thinking_get",
                {"artifact_type": "scratchpad", "artifact": "s1"},
                "architect_thinking_scratchpad_show",
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
        ])

        for case in cases:
            with self.subTest(canonical=case[1], expected=case[3]):
                self._assert_routes_to_schema(*case)


if __name__ == "__main__":
    unittest.main()
