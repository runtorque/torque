import tempfile
import unittest
from pathlib import Path

try:
    import ee_gate
except ModuleNotFoundError:  # Support `python -m unittest tests.<module>`.
    from tests import ee_gate


class EeTestGateTests(unittest.TestCase):
    def test_requires_explicit_torque_with_ee_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ee" / "python" / "torque_ee_connector").mkdir(parents=True)
            reason = ee_gate.ee_skip_reason(
                ["ee/python/torque_ee_connector"], env={}, root=root)
            enabled = ee_gate.ee_tests_enabled(
                ["ee/python/torque_ee_connector"], env={}, root=root)

        self.assertIn("TORQUE_WITH_EE=1", reason)
        self.assertFalse(enabled)

    def test_reports_missing_enterprise_paths_when_flag_is_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reason = ee_gate.ee_skip_reason(
                ["ee/python/torque_ee_connector"],
                env={"TORQUE_WITH_EE": "1"},
                root=root,
            )

        self.assertIn("missing enterprise paths", reason)
        self.assertIn("ee", reason)
        self.assertIn("ee/python/torque_ee_connector", reason)

    def test_enables_when_flag_and_required_paths_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ee" / "frontend" / "remote" / "js").mkdir(parents=True)
            enabled = ee_gate.ee_tests_enabled(
                ["ee/frontend/remote/js"],
                env={"TORQUE_WITH_EE": "true"},
                root=root,
            )

        self.assertTrue(enabled)


if __name__ == "__main__":
    unittest.main()
