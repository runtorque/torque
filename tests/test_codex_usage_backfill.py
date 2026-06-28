import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.codex_usage_backfill import (
    backfill_codex_provider_usage_for_dormant_agents,
    locate_latest_codex_rollout_for_workdir,
    locate_codex_rollout_for_session,
    refresh_codex_provider_usage_for_agents,
)
from torque.state import AgentCell, MatrixState


_CODEX_SESSION_ID = "019e6203-34e9-7353-98a8-df1d7a418905"


def _make_state(*cells):
    state = MatrixState()
    state.groups["g"] = []
    for cell in cells:
        state.agents[cell.id] = cell
    return state


def _codex_cell(
    cell_id="agent-1",
    *,
    agent_session_id=_CODEX_SESSION_ID,
    status="stopped",
    session_id=None,
):
    return AgentCell(
        id=cell_id,
        name="Codex",
        group="g",
        cell_type="agent",
        agent_type="codex",
        agent_session_id=agent_session_id,
        status=status,
        session_id=session_id,
    )


def _write_rollout(
    codex_home: Path,
    session_id: str = _CODEX_SESSION_ID,
    *,
    timestamp_label: str = "2026-05-26T14-00-00",
    used_percent: float = 42.4,
    mtime: float = 1779804000.0,
    cwd: str = "",
    include_rate_limits: bool = True,
) -> Path:
    directory = codex_home / "sessions" / "2026" / "05" / "26"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-{timestamp_label}-{session_id}.jsonl"
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {"total_tokens": 1799981},
            "last_token_usage": {
                "input_tokens": 147293,
                "cached_input_tokens": 139648,
                "output_tokens": 439,
                "reasoning_output_tokens": 16,
                "total_tokens": 147732,
            },
            "model_context_window": 258400,
        },
    }
    if include_rate_limits:
        payload["rate_limits"] = {
            "plan_type": "pro",
            "limit_id": "codex",
            "primary": {
                "used_percent": used_percent,
                "window_minutes": 300,
                "resets_at": 1779771600,
            },
            "secondary": {
                "used_percent": 12.2,
                "window_minutes": 10080,
                "resets_at": 1779787800,
            },
        }
    lines = []
    if cwd:
        lines.append(json.dumps({
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": cwd,
            },
        }))
    lines.append(json.dumps({"type": "event_msg", "payload": payload}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


class CodexUsageBackfillTests(unittest.TestCase):
    def test_backfills_dormant_codex_provider_usage_from_matching_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _write_rollout(codex_home)
            cell = _codex_cell(status="stopped", session_id=None)
            state = _make_state(cell)
            saved = []
            state._db_save_agent = lambda c: saved.append(c.id)

            changed = backfill_codex_provider_usage_for_dormant_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(changed, 1)
        self.assertEqual(
            cell.provider_usage,
            {
                "five_hour": {
                    "available": True,
                    "used_percentage": 42,
                    "resets_at": "2026-05-26T05:00:00Z",
                },
                "seven_day": {
                    "available": True,
                    "used_percentage": 12,
                    "resets_at": "2026-05-26T09:30:00Z",
                },
            },
        )
        self.assertEqual(cell.context_window["source"], "codex_transcript")
        self.assertEqual(cell.context_window["session_id"], _CODEX_SESSION_ID)
        self.assertEqual(cell.context_window["used_tokens"], 147732)
        self.assertEqual(cell.status, "stopped")
        self.assertIsNone(cell.session_id)
        self.assertEqual(saved, [cell.id])
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0]["provider_usage"], cell.provider_usage)
        self.assertIsNone(upserts[0]["session_id"])
        self.assertEqual(upserts[0]["status"], "stopped")

    def test_skips_missing_session_id_and_missing_rollout_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            no_session = _codex_cell("agent-1", agent_session_id="")
            no_rollout = _codex_cell(
                "agent-2",
                agent_session_id="019e6203-34e9-7353-98a8-df1d7a418999",
            )
            state = _make_state(no_session, no_rollout)

            changed = backfill_codex_provider_usage_for_dormant_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(changed, 0)
        self.assertIsNone(no_session.provider_usage)
        self.assertIsNone(no_rollout.provider_usage)
        self.assertEqual(
            [op for op in state._delta_ops if op["op"] == "agent_upsert"],
            [],
        )

    def test_active_codex_session_is_not_backfilled_or_mutated(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _write_rollout(codex_home)
            cell = _codex_cell(status="running", session_id="pty-session")
            state = _make_state(cell)

            changed = backfill_codex_provider_usage_for_dormant_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(changed, 0)
        self.assertIsNone(cell.provider_usage)
        self.assertEqual(cell.status, "running")
        self.assertEqual(cell.session_id, "pty-session")
        self.assertEqual(
            [op for op in state._delta_ops if op["op"] == "agent_upsert"],
            [],
        )

    def test_refresh_repairs_live_codex_session_from_matching_workdir_rollout(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex"
            workdir = Path(tmp) / "worktree"
            workdir.mkdir()
            _write_rollout(
                codex_home,
                session_id=_CODEX_SESSION_ID,
                cwd=str(workdir),
                mtime=1779804000.0,
            )
            cell = _codex_cell(
                status="running",
                session_id="pty-session",
                agent_session_id="",
            )
            cell.directory = str(workdir)
            cell.worktree_path = str(workdir)
            state = _make_state(cell)
            saved = []
            state._db_save_agent = lambda c: saved.append(c.id)

            report = refresh_codex_provider_usage_for_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(report.changed, 1)
        self.assertEqual(report.skipped_total, 0)
        self.assertEqual(cell.agent_session_id, _CODEX_SESSION_ID)
        self.assertEqual(cell.session_id, "pty-session")
        self.assertEqual(cell.status, "running")
        self.assertEqual(cell.provider_usage["five_hour"]["used_percentage"], 42)
        self.assertEqual(cell.context_window["session_id"], _CODEX_SESSION_ID)
        self.assertEqual(saved, [cell.id])
        upserts = [op for op in state._delta_ops if op["op"] == "agent_upsert"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0]["agent_session_id"], _CODEX_SESSION_ID)
        self.assertEqual(upserts[0]["session_id"], "pty-session")

    def test_refresh_reports_explicit_skip_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex"
            no_session = _codex_cell("no-session", agent_session_id="")
            no_rollout = _codex_cell(
                "no-rollout",
                agent_session_id="019e6203-34e9-7353-98a8-df1d7a418999",
            )
            no_rate_limits = _codex_cell(
                "no-rate-limits",
                agent_session_id=_CODEX_SESSION_ID,
            )
            _write_rollout(
                codex_home,
                include_rate_limits=False,
            )
            state = _make_state(no_session, no_rollout, no_rate_limits)

            report = refresh_codex_provider_usage_for_agents(
                state,
                codex_home=codex_home,
            )
            second = refresh_codex_provider_usage_for_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(report.changed, 1, "context_window still refreshes")
        self.assertEqual(report.skipped["no_agent_session_id"], 1)
        self.assertEqual(report.skipped["no_rollout"], 1)
        self.assertIn("Backfilled 1 / skipped 2", report.summary())
        self.assertIn("no_agent_session_id=1", report.summary())
        self.assertIn("no_rollout=1", report.summary())
        self.assertEqual(second.changed, 0)
        self.assertEqual(second.skipped["no_rate_limits"], 1)
        self.assertIsNone(no_session.provider_usage)
        self.assertIsNone(no_rollout.provider_usage)
        self.assertIsNone(no_rate_limits.provider_usage)

    def test_unchanged_provider_usage_does_not_emit_spurious_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _write_rollout(codex_home, mtime=1779804000.0)
            cell = _codex_cell()
            state = _make_state(cell)
            saved = []
            state._db_save_agent = lambda c: saved.append(c.id)

            first = backfill_codex_provider_usage_for_dormant_agents(
                state,
                codex_home=codex_home,
            )
            state._delta_ops.clear()
            saved.clear()
            second = backfill_codex_provider_usage_for_dormant_agents(
                state,
                codex_home=codex_home,
            )

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(saved, [])
        self.assertEqual(
            [op for op in state._delta_ops if op["op"] == "agent_upsert"],
            [],
        )

    def test_locate_uses_matching_session_suffix_and_latest_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            older = _write_rollout(
                codex_home,
                timestamp_label="2026-05-26T13-00-00",
                mtime=1779803000.0,
            )
            newer = _write_rollout(
                codex_home,
                timestamp_label="2026-05-26T14-00-00",
                used_percent=55,
                mtime=1779805000.0,
            )
            other = _write_rollout(
                codex_home,
                session_id="019e6203-34e9-7353-98a8-df1d7a410000",
                timestamp_label="2026-05-26T15-00-00",
                mtime=1779809000.0,
            )

            located = locate_codex_rollout_for_session(
                _CODEX_SESSION_ID,
                codex_home=codex_home,
            )

        self.assertEqual(located, newer)
        self.assertNotEqual(located, older)
        self.assertNotEqual(located, other)

    def test_locate_latest_rollout_for_workdir_extracts_full_session_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex"
            workdir = Path(tmp) / "worktree"
            other_dir = Path(tmp) / "other"
            workdir.mkdir()
            other_dir.mkdir()
            other = _write_rollout(
                codex_home,
                session_id="019e6203-34e9-7353-98a8-df1d7a410000",
                timestamp_label="2026-05-26T15-00-00",
                cwd=str(other_dir),
                mtime=1779809000.0,
            )
            expected = _write_rollout(
                codex_home,
                session_id=_CODEX_SESSION_ID,
                timestamp_label="2026-05-26T14-00-00",
                cwd=str(workdir),
                mtime=1779805000.0,
            )

            located = locate_latest_codex_rollout_for_workdir(
                [str(workdir)],
                codex_home=codex_home,
            )

        self.assertEqual(located, (expected, _CODEX_SESSION_ID))
        self.assertNotEqual(located[0], other)


if __name__ == "__main__":
    unittest.main()

class CodexUsageSdkSkipTests(unittest.TestCase):
    def test_sdk_cells_are_skipped_before_session_or_workdir_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            rollout = _write_rollout(codex_home, cwd="/same/repo")
            os.utime(rollout, (1779804000.0, 1779804000.0))
            sdk = _codex_cell(
                "sdk",
                agent_session_id=_CODEX_SESSION_ID,
                status="running",
                session_id="sdk-runtime",
            )
            sdk.runner_backend = "codex-sdk-readonly"
            sdk.directory = "/same/repo"
            state = _make_state(sdk)

            report = refresh_codex_provider_usage_for_agents(
                state,
                codex_home=codex_home,
                include_session_inference=True,
            )

            self.assertEqual(report.changed, 0)
            self.assertEqual(report.skipped["sdk_runner"], 1)
            self.assertIsNone(sdk.provider_usage)
            self.assertEqual(sdk.context_window, {})
            self.assertEqual(sdk.agent_session_id, _CODEX_SESSION_ID)

    def test_sdk_cells_with_no_thread_id_do_not_infer_from_same_workdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp)
            _write_rollout(codex_home, cwd="/same/repo")
            sdk = _codex_cell("sdk", agent_session_id="", session_id="sdk-runtime")
            sdk.runner_backend = "codex-sdk-readonly"
            sdk.directory = "/same/repo"
            state = _make_state(sdk)

            changed = refresh_codex_provider_usage_for_agents(
                state,
                codex_home=codex_home,
                include_session_inference=True,
            )

            self.assertEqual(changed.changed, 0)
            self.assertEqual(sdk.agent_session_id, "")
            self.assertIsNone(sdk.provider_usage)
