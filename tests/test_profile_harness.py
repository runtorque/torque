import importlib.util
import sys
import unittest
from pathlib import Path

from loom.profiling import ProfileRecorder, percentile, summarize


HARNESS_PATH = Path(__file__).resolve().parents[1] / "scripts" / "profile_harness.py"
spec = importlib.util.spec_from_file_location("profile_harness", HARNESS_PATH)
profile_harness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = profile_harness
spec.loader.exec_module(profile_harness)


class ProfilingMathTests(unittest.TestCase):
    def test_percentile_and_summary(self):
        self.assertEqual(percentile([1, 2, 3], 50), 2)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)
        stats = summarize([10, 20, 30])
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["p50"], 20)

    def test_profile_recorder_snapshot(self):
        recorder = ProfileRecorder(max_samples=3)
        # Enable-independent direct sample storage is intentionally gated in
        # production; exercise reset/snapshot shape with an empty recorder.
        snapshot = recorder.snapshot()
        self.assertIn("samples", snapshot)
        self.assertIn("counters", snapshot)
        self.assertIn("uptime_sec", snapshot)


class HarnessReportTests(unittest.TestCase):
    def test_parse_matrix(self):
        self.assertEqual(profile_harness.parse_matrix("10, 20;30"), [10, 20, 30])
        with self.assertRaises(ValueError):
            profile_harness.parse_matrix("")

    def test_compare_reports_flags_regression_and_win(self):
        baseline = {
            "runs": [{
                "n": 10,
                "hot_path": {
                    "event_loss_count": 0,
                    "sqlite_write_p95_ms": 10,
                    "event_loop_lag_p95_ms": 10,
                    "ws_broadcast_p95_ms": 10,
                    "ws_payload_p95_bytes": 100,
                    "event_endpoint_p95_ms": 10,
                    "snapshot_max_bytes": 1000,
                },
            }]
        }
        current = {
            "runs": [{
                "n": 10,
                "hot_path": {
                    "event_loss_count": 0,
                    "sqlite_write_p95_ms": 13,
                    "event_loop_lag_p95_ms": 7,
                    "ws_broadcast_p95_ms": 10,
                    "ws_payload_p95_bytes": 100,
                    "event_endpoint_p95_ms": 10,
                    "snapshot_max_bytes": 1000,
                },
            }]
        }
        comparison = profile_harness.compare_reports(
            baseline, current, threshold_pct=20)
        statuses = {row["metric"]: row["status"] for row in comparison["rows"]}
        self.assertEqual(statuses["sqlite_write_p95_ms"], "regression")
        self.assertEqual(statuses["event_loop_lag_p95_ms"], "win")

    def test_markdown_contains_acceptance_columns(self):
        report = {
            "mode": "baseline",
            "generated_at": "2026-04-22T00:00:00+00:00",
            "repo": {"branch": "main", "head": "abcdef123456"},
            "config": {"matrix": [10], "duration_sec": 1},
            "runs": [{
                "n": 10,
                "client": {"sent_events": 5},
                "server": {"counters": {"events_accepted": 5}, "cprofile_top": []},
                "acceptance": {
                    "event_loss_count": 0,
                    "snapshot_json_size_bytes": {"max": 100},
                    "ws_connect_latency_ms": {"p95": 1},
                    "event_loop_blocking_ms": {"p95": 2},
                    "sqlite_write_latency_ms": {"p95": 3},
                    "ws_payload_size_bytes": {"p95": 4},
                    "ws_broadcast_latency_ms": {"p95": 5},
                },
                "py_spy": {"status": "missing", "output": ""},
            }],
        }
        markdown = profile_harness.render_markdown(report)
        self.assertIn("Acceptance evidence", markdown)
        self.assertIn("SQLite p95", markdown)
        self.assertIn("snapshot max bytes", markdown)


if __name__ == "__main__":
    unittest.main()
