"""Architecture guardrails for the backend modularization boundaries."""

import ast
import builtins
from pathlib import Path
import unittest

from torque.worktree import WorktreeManager
from torque.worktree_manager.changes import ChangesMixin
from torque.worktree_manager.github import GithubMixin
from torque.worktree_manager.lifecycle import LifecycleMixin
from torque.worktree_manager.merge import MergeMixin
from torque.worktree_manager.nested_lifecycle import NestedLifecycleMixin
from torque.worktree_manager.nested_merge import NestedMergeMixin
from torque.worktree_manager.refresh import RefreshMixin


REPO_ROOT = Path(__file__).resolve().parents[1]


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
        budgets = {
            "torque/server.py": 6000,
            # Shared state contracts plus the MatrixState composition root.
            "torque/state.py": 5000,
            "torque/db.py": 2500,
            # Declarative schema inventory and ordered migration catalog.
            "torque/db_schema.py": 3800,
            # Read-only diagnostic collection and text rendering surface.
            "torque/doctor.py": 2600,
            "torque/mcp_tools_shared.py": 2500,
            "torque/worktree.py": 2500,
        }
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

    def test_no_unreviewed_backend_file_exceeds_2500_lines(self):
        reviewed_structural_files = {
            "torque/server.py",
            "torque/state.py",
            "torque/db_schema.py",
            "torque/doctor.py",
        }
        violations = []
        for path in (REPO_ROOT / "torque").rglob("*.py"):
            relative_path = str(path.relative_to(REPO_ROOT))
            line_count = len(path.read_text().splitlines())
            if line_count > 2500 and relative_path not in reviewed_structural_files:
                violations.append(f"{relative_path}: {line_count}")
        self.assertEqual(
            [],
            violations,
            "backend files above 2500 lines require an explicit architecture "
            "review and budget or must be split by responsibility",
        )

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
        class_budgets = {
            ("torque/state.py", "MatrixState"): 103,
            ("torque/db.py", "TorqueDB"): 50,
            ("torque/worktree.py", "WorktreeManager"): 1,
        }
        for (relative_path, class_name), maximum in class_budgets.items():
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


if __name__ == "__main__":
    unittest.main()
