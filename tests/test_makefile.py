import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MakefileInstallTests(unittest.TestCase):
    def test_install_dry_run_keeps_orjson_dependency_command_valid(self):
        if not shutil.which("make"):
            self.skipTest("make is not available")

        proc = subprocess.run(
            [
                "make",
                "-n",
                "install",
                "ITERM2_PROJECT=/tmp/torque-iterm2-test",
                "SCRIPT_DIR=/tmp/torque-iterm2-test/torque",
                "PROJECT_PYTHON=/usr/bin/printf",
                "GLOBAL_ENV=",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn(
            "aiohttp jinja2 pyyaml orjson 2>/dev/null || true",
            proc.stdout,
        )
        self.assertNotIn("/dev/nulle", proc.stdout)
        self.assertNotIn("\\true", proc.stdout)

    def test_test_ee_target_runs_explicit_enterprise_suite(self):
        text = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("test-ee:", text)
        self.assertIn("TORQUE_WITH_EE=1", text)
        for module in (
            "tests.test_ee_connector",
            "tests.test_ee_license_boundary",
            "tests.test_ee_python_package",
            "tests.test_relay_probe",
            "tests.test_frontend_remote",
        ):
            with self.subTest(module=module):
                self.assertIn(module, text)


if __name__ == "__main__":
    unittest.main()
