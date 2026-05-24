import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLBELT_DEPRECATION_NOTICE = "The iTerm2 Toolbelt is DEPRECATED"


class MakefileInstallTests(unittest.TestCase):
    def _run_make_dry(self, target, *extra_args):
        if not shutil.which("make"):
            self.skipTest("make is not available")

        return subprocess.run(
            ["make", "-n", target, *extra_args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_install_dry_run_uses_legacy_toolbelt_requirements(self):
        proc = self._run_make_dry(
            "install",
            "ITERM2_PROJECT=/tmp/torque-iterm2-test",
            "SCRIPT_DIR=/tmp/torque-iterm2-test/torque",
            "PROJECT_PYTHON=/usr/bin/printf",
            "GLOBAL_ENV=",
        )

        self.assertIn(
            "-m pip install -q -r \"requirements/toolbelt-legacy.txt\" 2>/dev/null",
            proc.stdout,
        )
        self.assertNotIn("/dev/nulle", proc.stdout)
        self.assertNotIn("\\true", proc.stdout)
        self.assertNotIn("aiohttp jinja2 pyyaml orjson", proc.stdout)

    def test_deps_dry_run_bootstraps_runtime_venv_without_iterm2_requirement(self):
        proc = self._run_make_dry(
            "deps",
            "TORQUE_BASE_PYTHON=/usr/bin/python3",
            "TORQUE_RUNTIME_VENV=/tmp/torque-runtime-test/venv",
        )

        self.assertIn("scripts/bootstrap_runtime_venv.py", proc.stdout)
        self.assertIn("--venv \"/tmp/torque-runtime-test/venv\"", proc.stdout)
        self.assertIn("--requirements \"requirements/desktop.txt\"", proc.stdout)
        self.assertNotIn("iTerm2 Python not found", proc.stdout)

    def test_toolbelt_entrypoints_dry_run_with_deprecation_notice(self):
        for target in ("install-toolbelt", "run-toolbelt", "deploy-toolbelt", "check"):
            with self.subTest(target=target):
                proc = self._run_make_dry(
                    target,
                    "FORCE=1",
                    "ITERM2_PROJECT=/tmp/torque-iterm2-test",
                    "SCRIPT_DIR=/tmp/torque-iterm2-test/torque",
                    "PRIMARY_APP_DIR=/tmp/torque-primary-test",
                    "PROJECT_PYTHON=/usr/bin/printf",
                    "GLOBAL_ENV=",
                    "GLOBAL_PYTHON=",
                    "ITERM2_PYTHON=/usr/bin/printf",
                    "TORQUE_RUNTIME_PYTHON=/usr/bin/printf",
                )

                self.assertIn(TOOLBELT_DEPRECATION_NOTICE, proc.stdout)
                self.assertIn("scripts/migrate_toolbelt_to_profile.py", proc.stdout)

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
