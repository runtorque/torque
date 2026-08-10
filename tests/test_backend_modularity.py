"""Architecture guardrails for the backend modularization boundaries."""

import ast
import builtins
import dis
import importlib
from pathlib import Path
import subprocess
import tempfile
import types
import typing
import unittest

from tests.helpers import install_aiohttp_stub
from torque.backend_invariants import (
    BACKEND_LINE_LIMITS,
    BackendInvariantCheckError,
    COMPATIBILITY_FACADE_METHOD_LIMITS,
    DEFAULT_BACKEND_LINE_LIMIT,
    backend_modularity_headroom,
    check_backend_modularity_crossings,
    format_backend_modularity_crossings,
    format_backend_modularity_headroom,
)
from torque.worktree import WorktreeManager
from torque.worktree_manager.changes import ChangesMixin
from torque.worktree_manager.github import GithubMixin
from torque.worktree_manager.lifecycle import LifecycleMixin
from torque.worktree_manager.merge import MergeMixin
from torque.worktree_manager.nested_lifecycle import NestedLifecycleMixin
from torque.worktree_manager.nested_merge import NestedMergeMixin
from torque.worktree_manager.refresh import RefreshMixin


REPO_ROOT = Path(__file__).resolve().parents[1]


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _assignment_names(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {
        target.id
        for target in targets
        if isinstance(target, ast.Name)
    }


def _external_ticket_offload_inventory(repo_root):
    """Derive blocking external-ticket operations and unsafe async callers."""
    torque_root = repo_root / "torque"
    external_path = torque_root / "external_tickets.py"
    external_tree = ast.parse(
        external_path.read_text(encoding="utf-8"),
        filename=str(external_path),
    )

    adapter_classes = {
        node.name: node
        for node in external_tree.body
        if isinstance(node, ast.ClassDef)
        and (
            node.name == "ExternalTicketAdapter"
            or any(
                isinstance(base, ast.Name)
                and base.id.endswith("ExternalTicketAdapter")
                for base in node.bases
            )
        )
    }
    class_methods = {}
    blocking_methods = set()
    for class_name, class_node in adapter_classes.items():
        methods = {
            node.name: node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        edges = {name: set() for name in methods}
        direct_sinks = set()
        for method_name, method in methods.items():
            for call in (
                node for node in ast.walk(method) if isinstance(node, ast.Call)
            ):
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "subprocess"
                    and call.func.attr == "run"
                ):
                    direct_sinks.add(method_name)
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                    and call.func.attr in methods
                ):
                    edges[method_name].add(call.func.attr)
        reachable = set(direct_sinks)
        changed = True
        while changed:
            changed = False
            for method_name, callees in edges.items():
                if method_name not in reachable and callees & reachable:
                    reachable.add(method_name)
                    changed = True
        class_methods[class_name] = sorted(reachable)
        blocking_methods.update(reachable)

    module_functions = {
        node.name: node
        for node in external_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    adapter_factories = {"get_adapter"}
    blocking_wrappers = set()
    changed = True
    while changed:
        changed = False
        for function_name, function in module_functions.items():
            if function_name in blocking_wrappers:
                continue
            adapter_variables = set()
            for node in ast.walk(function):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not node.value:
                    continue
                if (
                    isinstance(node.value, ast.Call)
                    and _call_name(node.value.func)
                    in adapter_factories | set(adapter_classes)
                ):
                    adapter_variables.update(_assignment_names(node))
            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                direct_adapter_dispatch = (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in blocking_methods
                    and (
                        (
                            isinstance(call.func.value, ast.Name)
                            and call.func.value.id in adapter_variables
                        )
                        or (
                            isinstance(call.func.value, ast.Call)
                            and _call_name(call.func.value.func)
                            in adapter_factories | set(adapter_classes)
                        )
                    )
                )
                wrapper_dispatch = (
                    isinstance(call.func, ast.Name)
                    and call.func.id in blocking_wrappers
                )
                if direct_adapter_dispatch or wrapper_dispatch:
                    blocking_wrappers.add(function_name)
                    changed = True
                    break

    offload_helpers = set()
    for function_name, function in module_functions.items():
        if not isinstance(function, ast.AsyncFunctionDef):
            continue
        if any(
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "asyncio"
            and call.func.attr == "to_thread"
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
        ):
            offload_helpers.add(function_name)

    parsed = {}
    bindings = {}
    for path in torque_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed[path] = tree
        file_bindings = {
            "wrappers": set(),
            "helpers": set(),
            "factories": set(),
            "classes": set(),
            "modules": set(),
        }
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (
                (node.module or "").endswith("external_tickets")
            ):
                for imported in node.names:
                    local_name = imported.asname or imported.name
                    if imported.name in blocking_wrappers:
                        file_bindings["wrappers"].add(local_name)
                    if imported.name in offload_helpers:
                        file_bindings["helpers"].add(local_name)
                    if imported.name in adapter_factories:
                        file_bindings["factories"].add(local_name)
                    if imported.name in adapter_classes:
                        file_bindings["classes"].add(local_name)
            elif isinstance(node, ast.Import):
                for imported in node.names:
                    if imported.name.endswith("external_tickets"):
                        file_bindings["modules"].add(
                            imported.asname or imported.name
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "torque"
            ):
                for imported in node.names:
                    if imported.name == "external_tickets":
                        file_bindings["modules"].add(
                            imported.asname or imported.name
                        )
        bindings[path] = file_bindings

    def reference_kind(node, file_bindings):
        if isinstance(node, ast.Name):
            for kind in ("wrappers", "helpers", "factories", "classes"):
                if node.id in file_bindings[kind]:
                    return kind
        if isinstance(node, ast.Attribute):
            owner = node.value
            if isinstance(owner, ast.Name) and owner.id in file_bindings["modules"]:
                if node.attr in blocking_wrappers:
                    return "wrappers"
                if node.attr in offload_helpers:
                    return "helpers"
                if node.attr in adapter_factories:
                    return "factories"
                if node.attr in adapter_classes:
                    return "classes"
        return ""

    # Preserve provenance while carrying ordinary aliases within each module.
    changed = True
    while changed:
        changed = False
        for path, tree in parsed.items():
            file_bindings = bindings[path]
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not node.value:
                    continue
                kind = reference_kind(node.value, file_bindings)
                if not kind:
                    continue
                new_names = _assignment_names(node) - file_bindings[kind]
                if new_names:
                    file_bindings[kind].update(new_names)
                    changed = True

    # Composition keywords establish runtime/object fields that carry a
    # blocking wrapper. Unlike local aliases, these intentionally cross files.
    wrapper_fields = {}
    for path, tree in parsed.items():
        file_bindings = bindings[path]
        for call in (
            node for node in ast.walk(tree) if isinstance(node, ast.Call)
        ):
            for keyword in call.keywords:
                if (
                    keyword.arg
                    and reference_kind(keyword.value, file_bindings) == "wrappers"
                ):
                    wrapper_fields.setdefault(keyword.arg, set()).add(
                        (str(path.relative_to(repo_root)), keyword.value.lineno)
                    )

    violations = []
    safe_sites = []
    for path, tree in parsed.items():
        relative_path = path.relative_to(repo_root)
        file_bindings = bindings[path]
        for function in ast.walk(tree):
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            local_wrappers = set(file_bindings["wrappers"])
            changed = True
            while changed:
                changed = False
                for node in ast.walk(function):
                    if (
                        not isinstance(node, (ast.Assign, ast.AnnAssign))
                        or not node.value
                    ):
                        continue
                    is_wrapper_reference = (
                        reference_kind(node.value, file_bindings) == "wrappers"
                        or (
                            isinstance(node.value, ast.Name)
                            and node.value.id in local_wrappers
                        )
                        or (
                            isinstance(node.value, ast.Attribute)
                            and node.value.attr in wrapper_fields
                        )
                    )
                    if not is_wrapper_reference:
                        continue
                    new_names = _assignment_names(node) - local_wrappers
                    if new_names:
                        local_wrappers.update(new_names)
                        changed = True
            adapter_variables = {
                argument.arg
                for argument in (
                    function.args.posonlyargs
                    + function.args.args
                    + function.args.kwonlyargs
                )
                if _call_name(argument.annotation) in file_bindings["classes"]
            }
            for node in ast.walk(function):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not node.value:
                    continue
                value = node.value
                if not isinstance(value, ast.Call):
                    continue
                called = _call_name(value.func)
                if called not in file_bindings["factories"] | file_bindings["classes"]:
                    continue
                adapter_variables.update(_assignment_names(node))

            for call in (
                node for node in ast.walk(function) if isinstance(node, ast.Call)
            ):
                called = _call_name(call.func)
                is_wrapper_call = (
                    isinstance(call.func, ast.Name)
                    and called in local_wrappers
                ) or (
                    isinstance(call.func, ast.Attribute)
                    and (
                        reference_kind(call.func, file_bindings) == "wrappers"
                        or called in wrapper_fields
                    )
                )
                is_adapter_call = (
                    isinstance(call.func, ast.Attribute)
                    and called in blocking_methods
                    and (
                        (
                            isinstance(call.func.value, ast.Name)
                            and call.func.value.id in adapter_variables
                        )
                        or (
                            isinstance(call.func.value, ast.Call)
                            and _call_name(call.func.value.func)
                            in file_bindings["factories"] | file_bindings["classes"]
                        )
                    )
                )
                if is_wrapper_call or is_adapter_call:
                    violations.append(
                        f"{relative_path}:{call.lineno} async {function.name} "
                        f"calls blocking external-ticket operation {called} directly"
                    )
                    continue

                is_offload = (
                    reference_kind(call.func, file_bindings) == "helpers"
                ) or (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "asyncio"
                    and called == "to_thread"
                )
                if not is_offload or not call.args:
                    if call.args:
                        operation_name = _call_name(call.args[0])
                        operation_is_blocking = (
                            reference_kind(call.args[0], file_bindings) == "wrappers"
                            or operation_name in local_wrappers | blocking_methods
                            or (
                                isinstance(call.args[0], ast.Attribute)
                                and call.args[0].attr in wrapper_fields
                            )
                        )
                        if operation_is_blocking:
                            violations.append(
                                f"{relative_path}:{call.lineno} async "
                                f"{function.name} passes blocking external-ticket "
                                f"operation {operation_name} through unrecognized "
                                f"boundary {called or '<call>'}"
                            )
                    continue
                operation_name = _call_name(call.args[0])
                operation_is_blocking = (
                    reference_kind(call.args[0], file_bindings) == "wrappers"
                    or operation_name in local_wrappers | blocking_methods
                    or (
                        isinstance(call.args[0], ast.Attribute)
                        and call.args[0].attr in wrapper_fields
                    )
                )
                if operation_is_blocking:
                    safe_sites.append(
                        f"{relative_path}:{call.lineno} async {function.name} "
                        f"offloads {operation_name} via {called}"
                    )

    return {
        "adapter_classes": class_methods,
        "blocking_methods": sorted(blocking_methods),
        "blocking_wrappers": sorted(blocking_wrappers),
        "offload_helpers": sorted(offload_helpers),
        "wrapper_fields": {
            name: sorted(origins) for name, origins in sorted(wrapper_fields.items())
        },
        "safe_sites": sorted(safe_sites),
        "violations": sorted(violations),
    }


class WorktreeManagerBoundaryTests(unittest.TestCase):
    def test_worktree_manager_composes_domain_mixins(self):
        ownership = (
            (RefreshMixin, "refresh_state"),
            (NestedLifecycleMixin, "_create_nested_submodule_worktrees"),
            (NestedMergeMixin, "nested_submodule_merge_preflight"),
            (LifecycleMixin, "create"),
            (ChangesMixin, "checkpoint"),
            (MergeMixin, "server_merge"),
            (GithubMixin, "create_pr"),
        )
        for mixin, method_name in ownership:
            with self.subTest(mixin=mixin.__name__, method=method_name):
                self.assertTrue(issubclass(WorktreeManager, mixin))
                self.assertIs(
                    getattr(WorktreeManager, method_name),
                    getattr(mixin, method_name),
                )


class BackendSizeGuardrailTests(unittest.TestCase):
    @staticmethod
    def _module_tree(relative_path: str) -> ast.Module:
        path = REPO_ROOT / relative_path
        return ast.parse(path.read_text(), filename=str(path))

    def test_composition_facades_stay_below_explicit_budgets(self):
        print(
            format_backend_modularity_headroom(
                backend_modularity_headroom(REPO_ROOT)
            )
        )
        self.assertEqual(DEFAULT_BACKEND_LINE_LIMIT, 2500)
        budgets = {
            "torque/server.py": 6000,
            # Shared state contracts plus the MatrixState composition root.
            "torque/state.py": 5000,
            "torque/db.py": 2500,
            # Declarative schema inventory and ordered migration catalog.
            "torque/db_schema.py": 3800,
            # Read-only diagnostic collection and text rendering surface.
            "torque/doctor.py": 2600,
            # Architecture-reviewed post-authorization/pre-write transport
            # seam for behavior-overlay conditional public argument validation.
            "torque/mcp.py": 2600,
            "torque/mcp_tools_shared.py": 2500,
            "torque/worktree.py": 2500,
        }
        self.assertEqual(
            BACKEND_LINE_LIMITS,
            {
                "torque/server.py": 6000,
                "torque/state.py": 5000,
                "torque/db_schema.py": 3800,
                "torque/doctor.py": 2600,
                "torque/mcp.py": 2600,
            },
        )
        for relative_path, maximum in budgets.items():
            with self.subTest(path=relative_path):
                line_count = len(
                    (REPO_ROOT / relative_path).read_text().splitlines()
                )
                self.assertLessEqual(
                    line_count,
                    maximum,
                    f"{relative_path} grew to {line_count} lines; move domain "
                    "behavior behind its existing composition boundary",
                )

    def test_green_guard_reports_current_headroom(self):
        measurements = backend_modularity_headroom(REPO_ROOT)
        report = format_backend_modularity_headroom(measurements)

        self.assertEqual(len(measurements), 8)
        self.assertEqual(
            [
                (item["kind"], item["path"], item.get("subject"))
                for item in measurements
            ],
            [
                ("method", "torque/state.py", "MatrixState"),
                ("method", "torque/db.py", "TorqueDB"),
                ("method", "torque/worktree.py", "WorktreeManager"),
                ("line", "torque/server.py", None),
                ("line", "torque/state.py", None),
                ("line", "torque/db_schema.py", None),
                ("line", "torque/doctor.py", None),
                ("line", "torque/mcp.py", None),
            ],
        )
        self.assertIn("method torque/state.py:MatrixState", report)
        self.assertIn("line torque/mcp.py", report)
        self.assertEqual(report.count("headroom "), 8)

    def test_no_unreviewed_backend_file_exceeds_2500_lines(self):
        violations = []
        for path in (REPO_ROOT / "torque").rglob("*.py"):
            relative_path = str(path.relative_to(REPO_ROOT))
            line_count = len(path.read_text().splitlines())
            maximum = BACKEND_LINE_LIMITS.get(
                relative_path,
                DEFAULT_BACKEND_LINE_LIMIT,
            )
            if line_count > maximum:
                violations.append(f"{relative_path}: {line_count}")
        self.assertEqual(
            [],
            violations,
            "backend files above 2500 lines require an explicit architecture "
            "review and budget or must be split by responsibility",
        )

    def test_responsibility_split_modules_have_singular_purposes(self):
        for relative_path in (
            "torque/backend_invariants.py",
            "torque/server_engineer_commands.py",
            "torque/server_user_commands.py",
            "torque/worktree_stream_readiness.py",
        ):
            with self.subTest(path=relative_path):
                purpose = ast.get_docstring(self._module_tree(relative_path))
                self.assertTrue(purpose)
                self.assertNotIn(" and ", purpose.lower())
                self.assertEqual(purpose.count("."), 1)


    def test_new_domain_modules_stay_below_2500_lines(self):
        domain_roots = (
            REPO_ROOT / "torque" / "commands",
            REPO_ROOT / "torque" / "mcp_scoped",
            REPO_ROOT / "torque" / "persistence",
            REPO_ROOT / "torque" / "services" / "worktrees",
            REPO_ROOT / "torque" / "worktree_manager",
        )
        for domain_root in domain_roots:
            for path in domain_root.glob("*.py"):
                with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                    self.assertLessEqual(
                        len(path.read_text().splitlines()),
                        2500,
                        f"split {path.relative_to(REPO_ROOT)} by responsibility",
                    )

    def test_domain_modules_do_not_import_compatibility_facades(self):
        forbidden = {
            "torque.server",
            "torque.db",
            "torque.mcp_tools_shared",
            "torque.worktree",
        }
        domain_roots = (
            REPO_ROOT / "torque" / "commands",
            REPO_ROOT / "torque" / "mcp_scoped",
            REPO_ROOT / "torque" / "persistence",
            REPO_ROOT / "torque" / "services" / "worktrees",
            REPO_ROOT / "torque" / "worktree_manager",
        )
        violations = []
        for domain_root in domain_roots:
            for path in domain_root.glob("*.py"):
                tree = ast.parse(path.read_text(), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = {alias.name for alias in node.names}
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            continue
                        names = {node.module or ""}
                    else:
                        continue
                    blocked = sorted(names & forbidden)
                    if blocked:
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)} imports {blocked}"
                        )
        self.assertEqual([], violations)

    def test_external_ticket_sync_operations_are_offloaded_from_async_callers(self):
        inventory = _external_ticket_offload_inventory(REPO_ROOT)

        self.assertTrue(inventory["blocking_methods"])
        self.assertTrue(inventory["blocking_wrappers"])
        self.assertEqual([], inventory["violations"])
        self.assertEqual(4, len(inventory["safe_sites"]))
        self.assertTrue(any(
            site.startswith("torque/commands/board_operations.py:")
            and "offloads import_external_ticket via to_thread" in site
            for site in inventory["safe_sites"]
        ))
        external_tree = self._module_tree("torque/external_tickets.py")
        helper = next(
            node for node in external_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in inventory["offload_helpers"]
        )
        helper_doc = ast.get_docstring(helper)
        self.assertIn("must not invoke", helper_doc)
        self.assertIn("asyncio.to_thread", helper_doc)
        self.assertIn("tests/test_backend_modularity.py", helper_doc)

    def test_external_ticket_offload_guard_rejects_synthetic_direct_async_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            torque = repo / "torque"
            commands = torque / "commands"
            commands.mkdir(parents=True)
            (torque / "external_tickets.py").write_text(
                "import asyncio\n"
                "import subprocess\n"
                "class ExternalTicketAdapter:\n"
                "    pass\n"
                "class GitHubExternalTicketAdapter(ExternalTicketAdapter):\n"
                "    def _run_gh(self, args):\n"
                "        return subprocess.run(args)\n"
                "    def publish(self, task):\n"
                "        return self._run_gh(['gh', task])\n"
                "def get_adapter():\n"
                "    return GitHubExternalTicketAdapter()\n"
                "def publish_ticket(task):\n"
                "    backend = get_adapter()\n"
                "    return backend.publish(task)\n"
                "async def run_external_ticket_operation(operation, *args):\n"
                "    return await asyncio.to_thread(operation, *args)\n",
                encoding="utf-8",
            )
            (commands / "violation.py").write_text(
                "from ..external_tickets import publish_ticket\n"
                "async def handle(task):\n"
                "    return publish_ticket(task)\n",
                encoding="utf-8",
            )
            (torque / "composition.py").write_text(
                "from .external_tickets import publish_ticket\n"
                "class Runtime:\n"
                "    def __init__(self, **kwargs):\n"
                "        self.__dict__.update(kwargs)\n"
                "runtime = Runtime(publish_ticket=publish_ticket)\n",
                encoding="utf-8",
            )
            (commands / "di_violation.py").write_text(
                "async def handle(runtime, task):\n"
                "    return runtime.publish_ticket(task)\n",
                encoding="utf-8",
            )
            (commands / "unrelated.py").write_text(
                "def publish_ticket(task):\n"
                "    return task\n"
                "async def handle(task):\n"
                "    return publish_ticket(task)\n",
                encoding="utf-8",
            )

            inventory = _external_ticket_offload_inventory(repo)

        self.assertIn("publish_ticket", inventory["blocking_wrappers"])
        self.assertEqual(
            [("torque/composition.py", 5)],
            inventory["wrapper_fields"]["publish_ticket"],
        )
        self.assertEqual(
            [
                "torque/commands/di_violation.py:2 async handle calls blocking "
                "external-ticket operation publish_ticket directly",
                "torque/commands/violation.py:3 async handle calls blocking "
                "external-ticket operation publish_ticket directly"
            ],
            inventory["violations"],
        )

    def test_scoped_mcp_dispatcher_is_a_domain_registry(self):
        path = REPO_ROOT / "torque" / "mcp_tools_shared.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        registry = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "SCOPED_DOMAIN_DISPATCHERS"
                for target in node.targets
            )
        )
        self.assertIsInstance(registry.value, ast.Tuple)
        names = [
            element.id
            for element in registry.value.elts
            if isinstance(element, ast.Name)
        ]
        self.assertEqual(
            [
                "dispatch_proposal",
                "dispatch_architect_reads",
                "dispatch_inventory",
                "dispatch_tasks",
                "dispatch_planning",
                "dispatch_communications",
                "dispatch_worktrees",
            ],
            names,
        )

    def test_compatibility_facades_have_bounded_direct_ownership(self):
        self.assertEqual(
            COMPATIBILITY_FACADE_METHOD_LIMITS,
            {
                ("torque/state.py", "MatrixState"): 103,
                ("torque/db.py", "TorqueDB"): 50,
                ("torque/worktree.py", "WorktreeManager"): 1,
            },
        )
        for (relative_path, class_name), maximum in (
            COMPATIBILITY_FACADE_METHOD_LIMITS.items()
        ):
            tree = self._module_tree(relative_path)
            class_node = next(
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            methods = [
                node.name
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            with self.subTest(path=relative_path, class_name=class_name):
                self.assertLessEqual(
                    len(methods),
                    maximum,
                    f"{class_name} gained direct behavior; extract it behind "
                    "an existing domain mixin/service",
                )
        worktree_tree = self._module_tree("torque/worktree.py")
        worktree_class = next(
            node
            for node in worktree_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "WorktreeManager"
        )
        self.assertEqual(
            ["__init__"],
            [
                node.name
                for node in worktree_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ],
        )

    def test_mcp_facade_owns_only_authenticated_dispatch_composition(self):
        tree = self._module_tree("torque/mcp_tools_shared.py")
        functions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(["dispatch_scoped_tool"], functions)

    def test_server_composition_root_has_bounded_command_routing(self):
        tree = self._module_tree("torque/server.py")
        main = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
        )
        handle_command = next(
            node
            for node in main.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "handle_command"
        )
        self.assertLessEqual(main.end_lineno - main.lineno + 1, 2600)
        self.assertLessEqual(
            handle_command.end_lineno - handle_command.lineno + 1,
            450,
            "move command semantics into torque.commands rather than growing "
            "the transport composition root",
        )

    def test_server_runtime_builders_do_not_capture_main_locals_implicitly(self):
        tree = self._module_tree("torque/server.py")
        module_bindings = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_bindings.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                module_bindings.update(
                    alias.asname or alias.name.split(".")[0]
                    for alias in node.names
                )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                module_bindings.update(
                    child.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Name)
                    and isinstance(child.ctx, ast.Store)
                )
        builtin_names = set(dir(builtins))
        violations = {}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_build_") or "runtime" not in node.name:
                continue
            parameters = {
                argument.arg
                for argument in (
                    node.args.posonlyargs
                    + node.args.args
                    + node.args.kwonlyargs
                )
            }
            loaded = {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
            }
            unresolved = sorted(
                loaded - parameters - module_bindings - builtin_names
            )
            if unresolved:
                violations[node.name] = unresolved
        self.assertEqual(
            {},
            violations,
            "runtime builders must receive main-local callbacks explicitly",
        )


class ResponsibilitySplitNameClosureTests(unittest.TestCase):
    MOVED_FUNCTIONS = {
        "torque.server_user_commands": (
            "_watch_local_response",
            "_handle_task_watch_command",
            "_parse_reminder_delay",
            "_reminder_local_response",
            "_handle_reminder_command",
        ),
        "torque.server_engineer_commands": (
            "_handle_engineer_flush_now_command",
            "_engineer_journal_source_key",
            "_append_engineer_journal_entry",
            "_handle_engineer_dismiss_note_command",
            "_handle_digest_pause_resume_command",
        ),
        "torque.worktree_stream_readiness": (
            "_normalize_repo_root",
            "invalidate_branch_exists_cache",
            "_merge_readiness_cache_key",
            "_merge_readiness_cache_get",
            "_merge_readiness_cache_put",
            "_list_repo_branches_async",
            "_refresh_repo_branches",
            "prefill_branch_exists_async",
            "_collect_state_repo_roots",
            "prefill_branch_exists_for_state",
            "_run_merge_readiness_git",
            "_probe_merge_readiness",
            "_stream_base_branch",
            "prefill_merge_readiness_for_state",
            "_branch_exists_locally",
        ),
    }

    @classmethod
    def _loaded_global_names(cls, code: types.CodeType) -> set[str]:
        names = {
            instruction.argval
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}
        }
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                names.update(cls._loaded_global_names(constant))
        return names

    def test_moved_functions_have_closed_body_and_annotation_names(self):
        install_aiohttp_stub()
        self.assertEqual(
            sum(len(names) for names in self.MOVED_FUNCTIONS.values()),
            25,
        )
        body_failures = {}
        annotation_failures = {}
        for module_name, function_names in self.MOVED_FUNCTIONS.items():
            module = importlib.import_module(module_name)
            for function_name in function_names:
                function = getattr(module, function_name)
                key = f"{module_name}.{function_name}"
                missing = sorted(
                    name
                    for name in self._loaded_global_names(function.__code__)
                    if name not in function.__globals__
                    and not hasattr(builtins, name)
                )
                if missing:
                    body_failures[key] = missing
                try:
                    typing.get_type_hints(function)
                except (NameError, TypeError) as exc:
                    annotation_failures[key] = f"{type(exc).__name__}: {exc}"
        self.assertEqual(
            {"body": {}, "annotations": {}},
            {
                "body": body_failures,
                "annotations": annotation_failures,
            },
        )


class BackendInvariantCrossingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        marker = self.repo / "tests" / "test_backend_modularity.py"
        marker.parent.mkdir()
        marker.write_text("# marker\n")
        backend = self.repo / "torque" / "sample.py"
        backend.parent.mkdir()
        backend.write_text("x = 1\n" * DEFAULT_BACKEND_LINE_LIMIT)
        self._write_policy()
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        self.base = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _commit_lines(self, count: int, message: str) -> str:
        (self.repo / "torque" / "sample.py").write_text("x = 1\n" * count)
        self._git("add", "torque/sample.py")
        self._git("commit", "-qm", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _write_policy(
        self,
        *,
        default: int = DEFAULT_BACKEND_LINE_LIMIT,
        sample: int | None = None,
    ) -> None:
        limits = (
            "{}"
            if sample is None
            else f'{{"torque/sample.py": {sample}}}'
        )
        (self.repo / "torque" / "backend_invariants.py").write_text(
            f"DEFAULT_BACKEND_LINE_LIMIT = {default}\n"
            f"BACKEND_LINE_LIMITS = {limits}\n"
        )

    def test_crossing_message_prescribes_budget_only_merge_first(self):
        message = format_backend_modularity_crossings({
            "crossings": [{
                "path": "torque/sample.py",
                "limit": DEFAULT_BACKEND_LINE_LIMIT,
                "base_lines": DEFAULT_BACKEND_LINE_LIMIT,
                "candidate_lines": DEFAULT_BACKEND_LINE_LIMIT + 1,
            }],
        })

        self.assertIn(
            "first merge an explicit architecture-reviewed budget without "
            "changing the target file; then base or rebase the target-growing "
            "candidate on the revision containing that budget",
            message,
        )
        self.assertIn("No daemon relaunch is required", message)

    def test_reports_only_a_new_limit_crossing(self):
        crossing = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "cross limit",
        )
        result = check_backend_modularity_crossings(
            self.repo,
            self.base,
            crossing,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["crossings"],
            [{
                "path": "torque/sample.py",
                "limit": DEFAULT_BACKEND_LINE_LIMIT,
                "base_lines": DEFAULT_BACKEND_LINE_LIMIT,
                "candidate_lines": DEFAULT_BACKEND_LINE_LIMIT + 1,
            }],
        )

    def test_budget_merged_on_base_applies_without_process_reload(self):
        self._write_policy(sample=DEFAULT_BACKEND_LINE_LIMIT + 100)
        self._git("add", "torque/backend_invariants.py")
        self._git("commit", "-qm", "merge reviewed sample budget")
        budget_base = self._git("rev-parse", "HEAD").stdout.strip()
        candidate = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "grow target after budget merge",
        )

        result = check_backend_modularity_crossings(
            self.repo,
            budget_base,
            candidate,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["crossings"], [])
        self.assertEqual(
            result["headroom"][0]["limit"],
            DEFAULT_BACKEND_LINE_LIMIT + 100,
        )

    def test_candidate_cannot_authorize_its_own_limit_raise(self):
        self._write_policy(sample=DEFAULT_BACKEND_LINE_LIMIT + 100)
        (self.repo / "torque" / "sample.py").write_text(
            "x = 1\n" * (DEFAULT_BACKEND_LINE_LIMIT + 1)
        )
        self._git("add", "torque")
        self._git("commit", "-qm", "raise budget and grow target together")
        candidate = self._git("rev-parse", "HEAD").stdout.strip()

        result = check_backend_modularity_crossings(
            self.repo,
            self.base,
            candidate,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["crossings"][0]["limit"],
            DEFAULT_BACKEND_LINE_LIMIT,
        )

    def test_default_limit_is_read_from_base_revision(self):
        self._write_policy(default=DEFAULT_BACKEND_LINE_LIMIT + 1)
        self._git("add", "torque/backend_invariants.py")
        self._git("commit", "-qm", "merge reviewed default budget")
        budget_base = self._git("rev-parse", "HEAD").stdout.strip()
        candidate = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "grow target to reviewed default",
        )

        result = check_backend_modularity_crossings(
            self.repo,
            budget_base,
            candidate,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["headroom"][0]["limit"],
            DEFAULT_BACKEND_LINE_LIMIT + 1,
        )

    def test_missing_base_policy_fails_closed(self):
        (self.repo / "torque" / "backend_invariants.py").unlink()
        self._git("add", "-A")
        self._git("commit", "-qm", "remove policy")
        missing_policy_base = self._git("rev-parse", "HEAD").stdout.strip()
        candidate = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "grow without readable policy",
        )

        with self.assertRaisesRegex(
            BackendInvariantCheckError,
            "could not read backend line-limit policy",
        ):
            check_backend_modularity_crossings(
                self.repo,
                missing_policy_base,
                candidate,
            )

    def test_malformed_base_policy_fails_closed(self):
        (self.repo / "torque" / "backend_invariants.py").write_text(
            "DEFAULT_BACKEND_LINE_LIMIT = 2500\n"
            "BACKEND_LINE_LIMITS = make_limits()\n"
        )
        self._git("add", "torque/backend_invariants.py")
        self._git("commit", "-qm", "malformed policy")
        malformed_policy_base = self._git("rev-parse", "HEAD").stdout.strip()
        candidate = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "grow with malformed policy",
        )

        with self.assertRaisesRegex(
            BackendInvariantCheckError,
            "invalid backend line-limit policy",
        ):
            check_backend_modularity_crossings(
                self.repo,
                malformed_policy_base,
                candidate,
            )

    def test_reports_nonblocking_headroom_warning_before_a_crossing(self):
        near_limit = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT - 6,
            "leave visible headroom",
        )

        result = check_backend_modularity_crossings(
            self.repo,
            self.base,
            near_limit,
        )

        expected = {
            "path": "torque/sample.py",
            "limit": DEFAULT_BACKEND_LINE_LIMIT,
            "base_lines": DEFAULT_BACKEND_LINE_LIMIT,
            "candidate_lines": DEFAULT_BACKEND_LINE_LIMIT - 6,
            "base_headroom": 0,
            "candidate_headroom": 6,
        }
        self.assertTrue(result["ok"])
        self.assertEqual(result["crossings"], [])
        self.assertEqual(result["headroom"], [expected])
        self.assertEqual(result["warnings"], [expected])

    def test_candidate_cannot_disable_gate_by_deleting_marker(self):
        (self.repo / "tests" / "test_backend_modularity.py").unlink()
        (self.repo / "torque" / "sample.py").write_text(
            "x = 1\n" * (DEFAULT_BACKEND_LINE_LIMIT + 1)
        )
        self._git("add", "-A")
        self._git("commit", "-qm", "delete marker while crossing limit")
        candidate = self._git("rev-parse", "HEAD").stdout.strip()

        result = check_backend_modularity_crossings(
            self.repo,
            self.base,
            candidate,
        )

        self.assertTrue(result["applicable"])
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["crossings"][0]["path"],
            "torque/sample.py",
        )

    def test_non_torque_base_remains_outside_repo_specific_gate(self):
        with tempfile.TemporaryDirectory() as non_torque_temp:
            repo = Path(non_torque_temp)

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            git("init", "-q", "-b", "main")
            git("config", "user.email", "test@example.com")
            git("config", "user.name", "Test")
            backend = repo / "torque" / "sample.py"
            backend.parent.mkdir()
            backend.write_text("x = 1\n" * DEFAULT_BACKEND_LINE_LIMIT)
            git("add", ".")
            git("commit", "-qm", "non-Torque baseline")
            base = git("rev-parse", "HEAD").stdout.strip()
            backend.write_text(
                "x = 1\n" * (DEFAULT_BACKEND_LINE_LIMIT + 1)
            )
            git("add", ".")
            git("commit", "-qm", "cross limit")
            candidate = git("rev-parse", "HEAD").stdout.strip()

            result = check_backend_modularity_crossings(
                repo,
                base,
                candidate,
            )

        self.assertTrue(result["ok"])
        self.assertFalse(result["applicable"])
        self.assertEqual(result["checked_files"], [])
        self.assertEqual(result["crossings"], [])

    def test_does_not_fire_when_file_remains_over_limit(self):
        already_over = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 1,
            "cross limit",
        )
        still_over = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT + 2,
            "remain over limit",
        )

        result = check_backend_modularity_crossings(
            self.repo,
            already_over,
            still_over,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["applicable"])
        self.assertEqual(result["crossings"], [])

    def test_does_not_fire_when_changed_file_stays_below_limit(self):
        below = self._commit_lines(
            DEFAULT_BACKEND_LINE_LIMIT - 1,
            "stay below",
        )
        result = check_backend_modularity_crossings(
            self.repo,
            self.base,
            below,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["crossings"], [])
        self.assertEqual(result["checked_files"], ["torque/sample.py"])


if __name__ == "__main__":
    unittest.main()
