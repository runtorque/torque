"""Tests for the mandatory-review enforcement contract (LOOM:256).

Covers:
- Action loader: ``transitions[].required`` and ``transitions[].pre_approved``
  parsed and exposed via ``get_transitions`` and ``has_required_transition``.
- BoardTask migration: legacy DBs default to ``requires_review=False`` and
  ``pre_approved_by=""``.
- Dispatch postscript: review-required block injected when
  ``requires_review=True``; pre-approved acknowledgement block injected
  when ``pre_approved_by`` is set.
- Server-side hard gate: ``loom_done`` / ``loom_ready`` refuse with
  ``review_required`` on tasks carrying ``requires_review=True`` and an
  empty ``pre_approved_by``; pass when ``pre_approved_by`` is set;
  ``loom_blocked`` / ``loom_ask`` / ``loom_error`` are not gated.
- Catalog: ``feature/implement`` declares the required transition;
  ``feature/review`` declares the two-shape transition list.
- Backwards-compat: existing actions and pre-existing tasks load without
  error.
"""

import importlib
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from loom.actions import ActionManager
from loom.db import LoomDB
from loom.db_board import (
    _BOARD_TASK_COLUMNS,
    _serialize_board_task,
    decode_board_task_row,
)
from loom.server_prompts import build_dispatch_postscript
from loom.state import BoardTask


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / ".loom" / "actions"


class ReviewRequiredActionLoaderTests(unittest.TestCase):
    def setUp(self):
        self.amgr = ActionManager()

    def test_action_without_required_flag_is_not_required(self):
        raw = """
name: oneshot/example
prompt: |
  {{ TASK }}
transitions:
  - action: feature/review
    when: maybe
"""
        # Use raw render for transitions parsing
        # via temp file simulation: write a tmp action and load via mgr.
        # Simpler: parse via render_action and inspect transitions directly.
        rendered = self.amgr.render_action(raw, {"TASK": "x"})
        self.assertEqual(rendered.get("transitions"), [
            {"action": "feature/review", "when": "maybe"}
        ])

    def test_required_and_pre_approved_round_trip_via_get_transitions(self):
        raw = """
name: feature/implement
prompt: |
  {{ TASK }}
transitions:
  - action: feature/review
    when: ready for review
    required: true
"""
        with tempfile.TemporaryDirectory() as tmp:
            actions_dir = Path(tmp) / ".loom" / "actions" / "feature"
            actions_dir.mkdir(parents=True)
            (actions_dir / "implement.yaml").write_text(raw)
            transitions = self.amgr.get_transitions(
                "feature/implement", base_dir=tmp)
            self.assertEqual(len(transitions), 1)
            self.assertTrue(transitions[0].get("required"))
            self.assertNotIn("pre_approved", transitions[0])
            self.assertTrue(self.amgr.has_required_transition(
                "feature/implement", base_dir=tmp))

    def test_pre_approved_transition_round_trip(self):
        raw = """
name: feature/review
prompt: |
  {{ TASK }}
transitions:
  - action: feature/implement
    when: blocking issue found
    target: parent
  - action: feature/implement
    when: small follow-up, no re-review
    target: parent
    pre_approved: true
"""
        with tempfile.TemporaryDirectory() as tmp:
            actions_dir = Path(tmp) / ".loom" / "actions" / "feature"
            actions_dir.mkdir(parents=True)
            (actions_dir / "review.yaml").write_text(raw)
            transitions = self.amgr.get_transitions(
                "feature/review", base_dir=tmp)
            self.assertEqual(len(transitions), 2)
            self.assertNotIn("pre_approved", transitions[0])
            self.assertTrue(transitions[1].get("pre_approved"))
            # No `required` flag in either entry — review's exit
            # transitions are not themselves required.
            self.assertFalse(any(t.get("required") for t in transitions))
            self.assertFalse(self.amgr.has_required_transition(
                "feature/review", base_dir=tmp))

    def test_find_transition_returns_pre_approved_entry(self):
        raw = """
name: feature/review
prompt: |
  {{ TASK }}
transitions:
  - action: feature/implement
    when: blocking
    target: parent
  - action: feature/implement
    when: pre-approved
    target: parent
    pre_approved: true
"""
        with tempfile.TemporaryDirectory() as tmp:
            actions_dir = Path(tmp) / ".loom" / "actions" / "feature"
            actions_dir.mkdir(parents=True)
            (actions_dir / "review.yaml").write_text(raw)
            # find_transition returns the FIRST entry; we just verify the
            # data is accessible and can be iterated by the caller. The
            # server-side derive logic walks all transitions and picks
            # the matching one with pre_approved set.
            tr = self.amgr.find_transition(
                "feature/review", "feature/implement", base_dir=tmp)
            self.assertIsNotNone(tr)
            self.assertEqual(tr.get("action"), "feature/implement")


class ReviewRequiredBoardTaskMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_migration_adds_review_columns_with_defaults(self):
        path = Path(self.tmp.name) / "legacy.db"
        db = LoomDB(path)
        self.addCleanup(db.close)
        db.init()

        # Insert a row, drop the new columns to simulate a pre-LOOM:256
        # database, then re-run init() and confirm the migration adds
        # them back with the correct defaults.
        db._conn.execute(
            "INSERT INTO board_tasks (id, task, group_name) "
            "VALUES (?, ?, ?)",
            ("legacy-1", "Pre-existing task", "g"),
        )
        for col in ("requires_review", "pre_approved_by"):
            db._conn.execute(
                f"ALTER TABLE board_tasks DROP COLUMN {col}")
        db._conn.commit()

        cols_before = {
            row[1]
            for row in db._conn.execute("PRAGMA table_info(board_tasks)")
        }
        self.assertNotIn("requires_review", cols_before)
        self.assertNotIn("pre_approved_by", cols_before)

        db.init()

        cols_after = {
            row[1]
            for row in db._conn.execute("PRAGMA table_info(board_tasks)")
        }
        for expected in ("requires_review", "pre_approved_by"):
            self.assertIn(expected, cols_after)

        rows = db._conn.execute(
            "SELECT requires_review, pre_approved_by FROM board_tasks"
        ).fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row, (0, ""))

    def test_serialize_and_decode_round_trip(self):
        bt = BoardTask(
            id="t-1",
            task="Implement feature X",
            group="g",
            requires_review=True,
            pre_approved_by="task-review-7",
        )
        values = _serialize_board_task(bt)
        decoded = decode_board_task_row(values, _BOARD_TASK_COLUMNS)
        self.assertTrue(decoded["requires_review"])
        self.assertEqual(decoded["pre_approved_by"], "task-review-7")

    def test_legacy_task_loads_with_default_review_fields(self):
        path = Path(self.tmp.name) / "loom.db"
        db = LoomDB(path)
        self.addCleanup(db.close)
        db.init()
        bt = BoardTask(id="t-leg", task="Pre-LOOM:256 task", group="g")
        db.save_board_task(bt)
        snapshot = db.load_all()
        loaded = snapshot["board_tasks"]["t-leg"]
        self.assertFalse(loaded["requires_review"])
        self.assertEqual(loaded["pre_approved_by"], "")


class ReviewRequiredPostscriptTests(unittest.TestCase):
    def test_no_block_when_not_required(self):
        ps = build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            requires_review=False,
        )
        self.assertNotIn("Review required", ps)
        self.assertNotIn("Review pre-approved", ps)

    def test_review_required_block_injected(self):
        ps = build_dispatch_postscript(
            transitions=[
                {"action": "feature/review",
                 "when": "ready",
                 "required": True}
            ],
            is_clean=True,
            requires_review=True,
        )
        self.assertIn("Review required", ps)
        self.assertIn("`feature/review`", ps)
        # The block precedes the completion-paths section.
        self.assertLess(
            ps.index("Review required"),
            ps.index("Available completion paths"),
        )
        # Pre-approved block is mutually exclusive.
        self.assertNotIn("Review pre-approved", ps)

    def test_pre_approved_block_injected_when_pre_approved_by_set(self):
        ps = build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            requires_review=True,
            pre_approved_by="task-review-42",
        )
        self.assertIn("Review pre-approved", ps)
        self.assertIn("task-review-42", ps)
        self.assertIn("loom_done", ps)
        self.assertNotIn("Review required", ps)


class ReviewRequiredGatePredicateTests(unittest.TestCase):
    """Unit tests for the gate's predicate."""

    def setUp(self):
        from loom.server import _reject_pending_review
        self._reject = _reject_pending_review

    def _task(self, **kwargs):
        return BoardTask(id="t", task="T", group="g", **kwargs)

    def test_no_contract_passes(self):
        task = self._task()
        self.assertIsNone(self._reject(task, "done"))

    def test_required_without_pre_approved_rejects_done(self):
        task = self._task(requires_review=True)
        result = self._reject(task, "done")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "review_required")
        self.assertIn("review required", result["message"])
        self.assertIn("loom_done", result["message"])
        # The error suggests the standard derive form.
        self.assertIn("loom_derive", result["message"])

    def test_required_without_pre_approved_rejects_ready(self):
        task = self._task(requires_review=True)
        result = self._reject(task, "ready")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "review_required")
        self.assertIn("loom_ready", result["message"])

    def test_required_with_pre_approved_passes(self):
        task = self._task(
            requires_review=True,
            pre_approved_by="task-review-9",
        )
        self.assertIsNone(self._reject(task, "done"))
        self.assertIsNone(self._reject(task, "ready"))

    def test_requires_review_false_passes(self):
        task = self._task(requires_review=False)
        self.assertIsNone(self._reject(task, "done"))


class CatalogActionsTests(unittest.TestCase):
    """The shipped catalog YAMLs declare the new contract correctly."""

    def setUp(self):
        self.mgr = ActionManager()

    def test_feature_implement_declares_required_review_transition(self):
        transitions = self.mgr.get_transitions(
            "feature/implement", base_dir=str(REPO_ROOT))
        review_transitions = [
            t for t in transitions if t.get("action") == "feature/review"
        ]
        self.assertEqual(len(review_transitions), 1)
        self.assertTrue(review_transitions[0].get("required"))
        # The action loader should also confirm the structural flag.
        self.assertTrue(self.mgr.has_required_transition(
            "feature/implement", base_dir=str(REPO_ROOT)))

    def test_feature_review_declares_blocking_and_pre_approved_transitions(self):
        transitions = self.mgr.get_transitions(
            "feature/review", base_dir=str(REPO_ROOT))
        impl_transitions = [
            t for t in transitions if t.get("action") == "feature/implement"
        ]
        self.assertEqual(len(impl_transitions), 2,
                         f"expected two transition shapes, got {transitions}")
        flagged = [t for t in impl_transitions if t.get("pre_approved")]
        unflagged = [t for t in impl_transitions if not t.get("pre_approved")]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(len(unflagged), 1)
        # Sanity: feature/review is itself NOT a required-transition action.
        self.assertFalse(self.mgr.has_required_transition(
            "feature/review", base_dir=str(REPO_ROOT)))

    def test_oneshot_actions_unchanged(self):
        # Backwards-compat: actions without required: should not emerge as
        # required-transition actions.
        for name in (
                "oneshot/fix", "oneshot/feature", "oneshot/improvement",
                "oneshot/investigate", "oneshot/audit",
                "feature/research"):
            with self.subTest(name=name):
                self.assertFalse(
                    self.mgr.has_required_transition(
                        name, base_dir=str(REPO_ROOT)),
                    f"action {name!r} unexpectedly carries a required "
                    "transition",
                )


class ReviewRequiredHandleCommandGateTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end: drive ``handle_command(cmd=ai_report)`` and confirm
    the gate refuses worker direct-done on a task with
    ``requires_review=True`` and an empty ``pre_approved_by``, then
    allows it once ``pre_approved_by`` is set."""

    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("loom.state")
        )
        self.actions_mod = importlib.reload(
            importlib.import_module("loom.actions")
        )
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server")
        )

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, *, action_mgr):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )

        class DummyBridge:
            async def list_profiles(self):
                return []

            async def get_launch_context(self):
                return types.SimpleNamespace(
                    current_path="",
                    current_profile="",
                )

            async def update_session(self, *_args, **_kwargs):
                return None

        class DummyWorktreeManager:
            async def diff_summary(self, _cell, *, non_test_only=False):
                return {"files": 0, "insertions": 0, "deletions": 0}

            async def checkpoint(self, _cell, *, message=""):
                return ""

        async def noop_async(*_args, **_kwargs):
            return None

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_broadcast_toast": noop_async,
            "_checkpoint_message": lambda _cell: "checkpoint",
            "_checkpoint_on_report": noop_async,
            "_cleanup_after_merge": noop_async,
            "_close_agent_session_only": noop_async,
            "_panel_event": lambda *args, **kwargs: None,
            "_record_task_boundary": noop_async,
            "_resolve_base_dir": noop_async,
            "_runtime_payload": lambda: {},
            "action_mgr": action_mgr,
            "bridge": DummyBridge(),
            "db": None,
            "handle_command": None,
            "panel_log": types.SimpleNamespace(
                replace_last=lambda *args, **kwargs: {}
            ),
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": DummyWorktreeManager(),
        })
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    def _build_state(self, *, requires_review: bool,
                     pre_approved_by: str = ""):
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            group="g",
            cell_type="agent",
            kind="worker",
            directory=str(REPO_ROOT),
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            "Implement feature X",
            "g",
            lane="In Progress",
            id="task-impl-1",
            action_name="feature/implement",
            agent_id=cell.id,
            requires_review=requires_review,
            pre_approved_by=pre_approved_by,
        )
        cell.current_task_id = task.id
        return state, cell, task

    async def test_loom_done_refused_when_review_required_and_no_bypass(self):
        state, cell, task = self._build_state(requires_review=True)
        action_mgr = self.actions_mod.ActionManager()
        handle_command = self._extract_handle_command(
            state, action_mgr=action_mgr)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "I did the thing",
        })
        self.assertEqual(
            result.get("type"), "review_required",
            f"expected review_required, got {result}",
        )
        # Task stays In Progress; the gate refuses without mutating lane.
        self.assertEqual(task.lane, "In Progress")

    async def test_loom_ready_refused_when_review_required_and_no_bypass(self):
        state, cell, task = self._build_state(requires_review=True)
        action_mgr = self.actions_mod.ActionManager()
        handle_command = self._extract_handle_command(
            state, action_mgr=action_mgr)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "ready",
            "message": "ready next",
        })
        self.assertEqual(result.get("type"), "review_required")
        self.assertEqual(task.lane, "In Progress")

    async def test_loom_done_allowed_when_pre_approved_by_set(self):
        state, cell, task = self._build_state(
            requires_review=True,
            pre_approved_by="task-review-9",
        )
        action_mgr = self.actions_mod.ActionManager()
        handle_command = self._extract_handle_command(
            state, action_mgr=action_mgr)

        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Pre-approved fix shipped",
        })
        self.assertNotEqual(
            result.get("type") if isinstance(result, dict) else None,
            "review_required",
            f"pre-approved bypass did not engage; got {result}",
        )
        # Bypassed gate moves task to Done.
        self.assertEqual(
            state.board_tasks[task.id].lane, "Done",
            f"expected Done, got {state.board_tasks[task.id].lane}",
        )

    async def test_blocked_and_progress_not_gated_by_review_required(self):
        state, cell, _task = self._build_state(requires_review=True)
        action_mgr = self.actions_mod.ActionManager()
        handle_command = self._extract_handle_command(
            state, action_mgr=action_mgr)

        # blocked is informational — no review gate.
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "blocked",
            "message": "stuck on a thing",
        })
        self.assertNotEqual(
            result.get("type") if isinstance(result, dict) else None,
            "review_required",
        )
        # progress is informational too.
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "progress",
            "message": "still working",
        })
        self.assertNotEqual(
            result.get("type") if isinstance(result, dict) else None,
            "review_required",
        )


if __name__ == "__main__":
    unittest.main()
