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
    DEFAULT_BACKEND_LINE_LIMIT,
    check_backend_modularity_crossings,
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
