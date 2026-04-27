"""Tests for the deliverable contract feature (LOOM:244).

Covers:
- Action loader: ``deliverable`` block parsed and normalized.
- BoardTask migration: legacy DBs default to ``deliverable_required=False``.
- Dispatch postscript: Deliverable contract block injected when required.
- Server-side hard gate: ``loom_done`` / ``loom_ready`` refuse without artifact;
  pass when one is attached; ``loom_blocked`` / ``loom_ask`` / ``loom_error``
  are not gated. Backward-compat: actions without ``deliverable`` and tasks
  without ``deliverable_required`` behave exactly as before.
"""

import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from loom.actions import ActionManager, normalize_deliverable
from loom.db import LoomDB
from loom.db_board import (
    _BOARD_TASK_COLUMNS,
    _serialize_board_task,
    decode_board_task_row,
)
from loom.server_prompts import build_dispatch_postscript, deliverable_word
from loom.state import BoardTask


class DeliverableActionLoaderTests(unittest.TestCase):
    def setUp(self):
        self.amgr = ActionManager()

    def test_action_without_block_normalizes_to_empty_contract(self):
        raw = """
name: research
prompt: |
  {{ TASK }}
"""
        rendered = self.amgr.render_action(raw, {"TASK": "Look into X"})
        self.assertEqual(
            rendered["deliverable"],
            {"required": False, "type": "", "format": "",
             "artifact_title": ""},
        )

    def test_action_block_parsed_with_defaults(self):
        raw = """
name: research
prompt: |
  {{ TASK }}
deliverable:
  required: true
  type: report
  format: markdown
  artifact_title: My research report
"""
        rendered = self.amgr.render_action(raw, {"TASK": "Investigate"})
        self.assertEqual(
            rendered["deliverable"],
            {
                "required": True,
                "type": "report",
                "format": "markdown",
                "artifact_title": "My research report",
            },
        )

    def test_normalize_deliverable_handles_partial_blocks(self):
        contract = normalize_deliverable({"required": True})
        self.assertTrue(contract["required"])
        self.assertEqual(contract["type"], "")
        self.assertEqual(contract["format"], "")

    def test_normalize_deliverable_coerces_truthy_strings(self):
        # YAML-ish strings should be accepted in case the YAML loader hands
        # us textual booleans (e.g. environments without bool coercion).
        for raw in (True, "true", "1", "yes"):
            self.assertTrue(
                normalize_deliverable({"required": raw})["required"],
                msg=f"expected truthy for {raw!r}",
            )
        for raw in (False, "", "0", "no", None):
            self.assertFalse(
                normalize_deliverable({"required": raw})["required"],
                msg=f"expected falsy for {raw!r}",
            )

    def test_get_deliverable_returns_empty_for_unknown_action(self):
        self.assertEqual(
            self.amgr.get_deliverable("does-not-exist"),
            {"required": False, "type": "", "format": "",
             "artifact_title": ""},
        )


class DeliverableBoardTaskMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_migration_adds_deliverable_columns_with_defaults(self):
        # Initialize a fresh DB (which lays down the new schema including
        # the deliverable columns), then drop the four columns to simulate
        # a pre-deliverable database, and re-run init() to confirm the
        # migration adds them back with the correct defaults.
        path = Path(self.tmp.name) / "legacy.db"
        db = LoomDB(path)
        self.addCleanup(db.close)
        db.init()

        # Insert a pre-deliverable row before we drop the columns.
        db._conn.execute(
            "INSERT INTO board_tasks (id, task, group_name) "
            "VALUES (?, ?, ?)",
            ("legacy-1", "Pre-existing task", "g"),
        )
        for col in (
                "deliverable_required",
                "deliverable_type",
                "deliverable_format",
                "deliverable_artifact_title",
        ):
            db._conn.execute(f"ALTER TABLE board_tasks DROP COLUMN {col}")
        db._conn.commit()

        cols_before = {
            row[1]
            for row in db._conn.execute("PRAGMA table_info(board_tasks)")
        }
        self.assertNotIn("deliverable_required", cols_before)

        # Re-run init() — the migration should detect missing columns and
        # add them.
        db.init()

        cols_after = {
            row[1]
            for row in db._conn.execute("PRAGMA table_info(board_tasks)")
        }
        for expected in (
                "deliverable_required",
                "deliverable_type",
                "deliverable_format",
                "deliverable_artifact_title",
        ):
            self.assertIn(expected, cols_after)

        rows = db._conn.execute(
            "SELECT deliverable_required, deliverable_type, "
            "deliverable_format, deliverable_artifact_title "
            "FROM board_tasks"
        ).fetchall()
        self.assertTrue(rows, "expected at least one pre-existing row")
        for row in rows:
            self.assertEqual(row, (0, "", "", ""))

    def test_serialize_and_decode_round_trip(self):
        bt = BoardTask(
            id="t-1",
            task="Audit feature",
            group="g",
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="Audit report",
        )
        values = _serialize_board_task(bt)
        decoded = decode_board_task_row(values, _BOARD_TASK_COLUMNS)
        self.assertTrue(decoded["deliverable_required"])
        self.assertEqual(decoded["deliverable_type"], "report")
        self.assertEqual(decoded["deliverable_format"], "markdown")
        self.assertEqual(
            decoded["deliverable_artifact_title"], "Audit report")


class DeliverablePostscriptTests(unittest.TestCase):
    def test_no_block_when_not_required(self):
        ps = build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            deliverable_required=False,
        )
        self.assertNotIn("Deliverable contract", ps)
        self.assertIn("Available completion paths", ps)

    def test_block_injected_when_required(self):
        ps = build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="MCP audit report",
            task_title="MCP tool surface audit",
        )
        self.assertIn("Deliverable contract", ps)
        self.assertIn("loom_task_upload_artifact", ps)
        self.assertIn("MCP audit report", ps)
        self.assertIn("report", ps)
        self.assertIn("markdown", ps)
        # Block precedes the completion-paths section
        self.assertLess(
            ps.index("Deliverable contract"),
            ps.index("Available completion paths"),
        )

    def test_block_falls_back_to_task_title_for_default_artifact_title(self):
        ps = build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            deliverable_required=True,
            deliverable_type="report",
            deliverable_artifact_title="",
            task_title="Audit MCP surface",
        )
        self.assertIn('title="Audit MCP surface"', ps)


class DeliverableWordTests(unittest.TestCase):
    """``deliverable_word`` derives the user-facing noun from contract type."""

    def test_canonical_types_returned_verbatim(self):
        for t in ("report", "plan", "inventory", "spec"):
            self.assertEqual(deliverable_word(t), t)

    def test_other_collapses_to_generic(self):
        self.assertEqual(deliverable_word("other"), "deliverable")

    def test_empty_collapses_to_generic(self):
        self.assertEqual(deliverable_word(""), "deliverable")
        self.assertEqual(deliverable_word("   "), "deliverable")
        self.assertEqual(deliverable_word(None), "deliverable")

    def test_other_is_case_insensitive(self):
        self.assertEqual(deliverable_word("Other"), "deliverable")
        self.assertEqual(deliverable_word("OTHER"), "deliverable")

    def test_freeform_type_returned_verbatim(self):
        self.assertEqual(
            deliverable_word("diagnostic_log"), "diagnostic_log")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(deliverable_word("  plan  "), "plan")


class DeliverablePostscriptTypeAwareCopyTests(unittest.TestCase):
    """Postscript natural-language copy must reflect the contract's type."""

    def _ps(self, deliverable_type: str) -> str:
        return build_dispatch_postscript(
            transitions=[],
            is_clean=True,
            deliverable_required=True,
            deliverable_type=deliverable_type,
            deliverable_format="markdown",
            deliverable_artifact_title="The thing",
            task_title="Some task",
        )

    def test_canonical_types_render_their_noun(self):
        cases = {
            "report": "report",
            "plan": "plan",
            "inventory": "inventory",
            "spec": "spec",
        }
        for type_, noun in cases.items():
            ps = self._ps(type_)
            self.assertIn(
                f"attach your {noun} to the task",
                ps,
                msg=f"type={type_!r}",
            )
            self.assertIn(
                f'<your full {noun}>',
                ps,
                msg=f"type={type_!r}",
            )

    def test_other_falls_back_to_generic(self):
        ps = self._ps("other")
        self.assertIn("attach your deliverable to the task", ps)
        self.assertIn('<your full deliverable>', ps)
        self.assertNotIn("attach your other", ps)
        self.assertNotIn("<your full other>", ps)

    def test_empty_type_falls_back_to_generic(self):
        ps = self._ps("")
        self.assertIn("attach your deliverable to the task", ps)
        self.assertIn('<your full deliverable>', ps)

    def test_freeform_type_renders_verbatim(self):
        ps = self._ps("diagnostic_log")
        self.assertIn("attach your diagnostic_log to the task", ps)
        self.assertIn('<your full diagnostic_log>', ps)


class DeliverableArtifactMatchTests(unittest.TestCase):
    """Unit tests for the gate's artifact-matching predicate."""

    def setUp(self):
        # Defer import: server.py touches aiohttp on import.
        from loom.server import (
            _reject_missing_deliverable,
            _task_has_matching_deliverable_artifact,
        )

        self._has_match = _task_has_matching_deliverable_artifact
        self._reject = _reject_missing_deliverable

    def _task(self, **kwargs):
        return BoardTask(id="t", task="T", group="g", **kwargs)

    def test_no_contract_passes(self):
        task = self._task()
        self.assertIsNone(self._reject(task, "done"))

    def test_required_without_artifact_rejects_done(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="report",
        )
        result = self._reject(task, "done")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "deliverable_missing")
        self.assertIn("type=report", result["message"])

    def test_required_without_artifact_rejects_ready(self):
        task = self._task(deliverable_required=True)
        result = self._reject(task, "ready")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "deliverable_missing")
        self.assertIn("ready", result["message"])

    def test_matching_artifact_passes(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="report",
            artifacts=[{"type": "report", "title": "x"}],
        )
        self.assertTrue(self._has_match(task))
        self.assertIsNone(self._reject(task, "done"))

    def test_non_matching_artifact_does_not_satisfy_typed_contract(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="report",
            artifacts=[{"type": "log", "title": "x"}],
        )
        self.assertFalse(self._has_match(task))
        result = self._reject(task, "done")
        self.assertIsNotNone(result)

    def test_empty_type_accepts_any_artifact(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="",
            artifacts=[{"type": "log", "title": "x"}],
        )
        self.assertTrue(self._has_match(task))
        self.assertIsNone(self._reject(task, "done"))

    def test_other_type_accepts_any_artifact(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="other",
            artifacts=[{"type": "snippet", "title": "x"}],
        )
        self.assertIsNone(self._reject(task, "done"))

    def test_attachment_artifact_type_field_also_accepted(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="report",
            attachments=[{"artifact_type": "report"}],
        )
        self.assertTrue(self._has_match(task))


class DeliverableGateErrorTypeAwareCopyTests(unittest.TestCase):
    """Gate error message must use the contract's deliverable noun."""

    def setUp(self):
        from loom.server import _reject_missing_deliverable

        self._reject = _reject_missing_deliverable

    def _task(self, **kwargs):
        return BoardTask(id="t", task="T", group="g", **kwargs)

    def test_canonical_types_render_their_noun_in_message(self):
        for noun in ("report", "plan", "inventory", "spec"):
            task = self._task(
                deliverable_required=True,
                deliverable_type=noun,
            )
            msg = self._reject(task, "done")["message"]
            self.assertIn(f"<your full {noun}>", msg, msg=f"type={noun!r}")
            self.assertIn(f"type={noun}", msg, msg=f"type={noun!r}")

    def test_other_falls_back_to_generic_noun(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="other",
        )
        msg = self._reject(task, "done")["message"]
        self.assertIn("<your full deliverable>", msg)
        self.assertNotIn("<your full other>", msg)
        self.assertNotIn("<your full report>", msg)

    def test_empty_type_falls_back_to_generic_noun(self):
        # Empty type means "any artifact" satisfies — but if none is
        # attached the gate still fires (legacy required-without-type
        # surface) and the natural-language noun should be generic.
        task = self._task(deliverable_required=True)
        msg = self._reject(task, "done")["message"]
        self.assertIn("<your full deliverable>", msg)
        self.assertNotIn("<your full report>", msg)

    def test_freeform_type_renders_verbatim(self):
        task = self._task(
            deliverable_required=True,
            deliverable_type="diagnostic_log",
        )
        msg = self._reject(task, "done")["message"]
        self.assertIn("<your full diagnostic_log>", msg)
        self.assertIn("type=diagnostic_log", msg)

    def test_no_legacy_hardcoded_report_noun_remains(self):
        # Regression guard: the old hardcoded "<your full report>" must
        # not leak through for non-report contracts.
        task = self._task(
            deliverable_required=True,
            deliverable_type="plan",
        )
        msg = self._reject(task, "done")["message"]
        self.assertNotIn("<your full report>", msg)


class DeliverableBoardTaskRoundTripTests(unittest.TestCase):
    """End-to-end persistence: BoardTask -> SQLite -> reload."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "loom.db"
        self.db = LoomDB(path)
        self.addCleanup(self.db.close)
        self.db.init()

    def test_board_task_persists_deliverable_fields(self):
        bt = BoardTask(
            id="t-deliv-1",
            task="Audit MCP surface",
            group="loom",
            deliverable_required=True,
            deliverable_type="report",
            deliverable_format="markdown",
            deliverable_artifact_title="Courier audit report",
        )
        self.db.save_board_task(bt)

        snapshot = self.db.load_all()
        loaded = snapshot["board_tasks"]["t-deliv-1"]
        self.assertTrue(loaded["deliverable_required"])
        self.assertEqual(loaded["deliverable_type"], "report")
        self.assertEqual(loaded["deliverable_format"], "markdown")
        self.assertEqual(
            loaded["deliverable_artifact_title"], "Courier audit report")

    def test_legacy_task_loads_with_default_contract(self):
        bt = BoardTask(id="t-leg-1", task="Pre-deliverable task", group="g")
        self.db.save_board_task(bt)
        snapshot = self.db.load_all()
        loaded = snapshot["board_tasks"]["t-leg-1"]
        self.assertFalse(loaded["deliverable_required"])
        self.assertEqual(loaded["deliverable_type"], "")
        self.assertEqual(loaded["deliverable_format"], "")
        self.assertEqual(loaded["deliverable_artifact_title"], "")


class DeliverableCliFailureSurfaceTests(unittest.TestCase):
    """The documented `loom ai done` / `loom ai ready` paths must exit
    with a non-zero status when the server refuses with
    ``deliverable_missing``. Without this, workers using the CLI would
    see a fake "Done" message even though the gate refused completion.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from importlib.machinery import SourceFileLoader

        path = (
            Path(__file__).resolve().parents[1] / "bin" / "loom"
        )
        loader = SourceFileLoader("loom_cli_deliverable", str(path))
        spec = importlib.util.spec_from_loader(
            "loom_cli_deliverable", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        cls.cli = mod

    def _setup_self_resolution(self):
        self.cli._resolve_self_and_task = lambda _args, **_kwargs: (
            {"id": "agent-1"},
            {"id": "task-1", "slug": "task-1"},
        )

    def _refusal_response(self):
        return {
            "ok": False,
            "type": "deliverable_missing",
            "error": (
                "Cannot mark task done: deliverable required "
                "(type=report) but no matching artifact attached. "
                "Call `loom_task_upload_artifact(...)` first, then "
                "retry loom_done."
            ),
        }

    def test_cli_done_exits_when_server_refuses_with_deliverable_missing(self):
        from types import SimpleNamespace
        import contextlib, io

        self._setup_self_resolution()
        captured_calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            captured_calls.append((cmd, kwargs))
            return self._refusal_response()

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(
            port=18932,
            message="Done!",
            post_external=False,
            force_skip_review=False,
            review_skip_reason="",
        )
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as ctx:
                self.cli.cmd_ai_done(args)
        # die() exits with 1
        self.assertEqual(ctx.exception.code, 1)
        # Operator-facing error text propagates
        self.assertIn("deliverable required", stderr.getvalue())
        # stdout did NOT show a fake "Done" line
        self.assertNotIn("Done", stdout.getvalue())
        # The call still went out (so we know the test exercises the
        # right code path)
        self.assertEqual(captured_calls[0][0], "ai_report")
        self.assertEqual(captured_calls[0][1]["action"], "done")

    def test_cli_ready_exits_when_server_refuses_with_deliverable_missing(self):
        from types import SimpleNamespace
        import contextlib, io

        self._setup_self_resolution()
        captured_calls = []

        def fake_api_call(cmd, port=0, **kwargs):
            captured_calls.append((cmd, kwargs))
            return {
                "ok": False,
                "type": "deliverable_missing",
                "error": (
                    "Cannot mark task ready: deliverable required "
                    "(type=report) but no matching artifact attached."
                ),
            }

        self.cli.api_call = fake_api_call
        args = SimpleNamespace(port=18932)
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), \
                contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as ctx:
                self.cli.cmd_ai_ready(args)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("deliverable required", stderr.getvalue())
        # stdout did NOT show a fake "Ready for next task" line
        self.assertNotIn("Ready for next task", stdout.getvalue())
        self.assertEqual(captured_calls[0][1]["action"], "ready")


if __name__ == "__main__":
    unittest.main()
