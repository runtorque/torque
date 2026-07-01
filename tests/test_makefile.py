import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    def test_install_standalone_dry_run_uses_primary_app_surface(self):
        proc = self._run_make_dry(
            "install-standalone",
            "PRIMARY_APP_DIR=/tmp/torque-primary-test",
        )

        self.assertIn("Installed primary standalone/desktop app files", proc.stdout)
        self.assertIn("/tmp/torque-primary-test", proc.stdout)
        self.assertNotIn("requirements/toolbelt-legacy.txt", proc.stdout)
        self.assertNotIn("ITERM2_PROJECT", proc.stdout)

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

    def test_ai_deps_dry_run_installs_optional_requirements_only_on_ai_target(self):
        proc = self._run_make_dry(
            "ai-deps",
            "TORQUE_BASE_PYTHON=/usr/bin/python3",
            "TORQUE_RUNTIME_VENV=/tmp/torque-runtime-test/venv",
        )

        self.assertIn("--requirements \"requirements/desktop.txt\"", proc.stdout)
        self.assertIn("-m pip install -r \"requirements/ai.txt\"", proc.stdout)

        deploy = self._run_make_dry(
            "deploy",
            "TORQUE_BASE_PYTHON=/usr/bin/python3",
            "TORQUE_RUNTIME_VENV=/tmp/torque-runtime-test/venv",
        )
        self.assertNotIn("requirements/ai.txt", deploy.stdout)

        self.assertNotIn(
            "requirements/ai.txt",
            (ROOT / "requirements" / "daemon.txt").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "requirements/ai.txt",
            (ROOT / "requirements" / "desktop.txt").read_text(encoding="utf-8"),
        )

    def test_toolbelt_deploy_install_run_surface_removed(self):
        text = (ROOT / "Makefile").read_text(encoding="utf-8")

        for target in (
            "install",
            "install-toolbelt",
            "run-toolbelt",
            "deploy-toolbelt",
            "_toolbelt_deprecation_notice",
        ):
            with self.subTest(target=target):
                self.assertNotRegex(text, rf"(?m)^{target}:")

        for snippet in (
            "TOOLBELT_DEPRECATION_NOTICE",
            "TOOLBELT_PORT",
            "TORQUE_TOOLBELT_REQUIREMENTS",
            "requirements/toolbelt-legacy.txt",
            "ITERM2_SCRIPTS",
            "ITERM2_PROJECT",
            "SCRIPT_DIR     := $(ITERM2_PROJECT)/torque",
        ):
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, text)

    def test_check_dry_run_reports_primary_app_only(self):
        proc = self._run_make_dry(
            "check",
            "PRIMARY_APP_DIR=/tmp/torque-primary-test",
            "TORQUE_RUNTIME_PYTHON=/usr/bin/printf",
        )

        self.assertIn("Primary app:  /tmp/torque-primary-test", proc.stdout)
        self.assertIn("Primary app installed:", proc.stdout)
        for snippet in (
            "The iTerm2 Toolbelt is DEPRECATED",
            "iTerm2 Python (legacy Toolbelt)",
            "Toolbelt installed:",
            "deploy-toolbelt",
            "iterm2env:",
            "setup.cfg:",
        ):
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, proc.stdout)

    def test_standalone_bg_pid_file_uses_profile_data_dir(self):
        proc = self._run_make_dry(
            "standalone-bg",
            "TORQUE_RUNTIME_PYTHON=/usr/bin/python3",
            "PRIMARY_APP_DIR=/tmp/torque-primary-test",
            "TORQUE_DATA_DIR=",
            "TORQUE_PROFILE=standalone",
            "TORQUE_PORT=18932",
        )

        self.assertIn('pid_file="$data_dir/torque.pid"', proc.stdout)
        self.assertNotIn("Application Support/iTerm2", proc.stdout)
        self.assertNotIn("SCRIPT_DIR", proc.stdout)

    def test_runtime_launch_targets_default_profile_when_unset(self):
        for target in ("standalone", "standalone-bg", "desktop", "desktop-attach"):
            with self.subTest(target=target):
                proc = self._run_make_dry(
                    target,
                    "TORQUE_RUNTIME_PYTHON=/usr/bin/python3",
                    "PRIMARY_APP_DIR=/tmp/torque-primary-test",
                    "TORQUE_DATA_DIR=",
                    "TORQUE_PROFILE=",
                    "TORQUE_PORT=",
                )

                self.assertIn('profile="default"', proc.stdout)
                self.assertIn('data_dir="$HOME/.torque/profiles/$safe_profile"', proc.stdout)

    def test_runtime_launch_preserves_explicit_profile(self):
        proc = self._run_make_dry(
            "desktop",
            "TORQUE_RUNTIME_PYTHON=/usr/bin/python3",
            "PRIMARY_APP_DIR=/tmp/torque-primary-test",
            "TORQUE_DATA_DIR=",
            "TORQUE_PROFILE=qa-profile",
            "TORQUE_PORT=",
        )

        self.assertIn('profile="qa-profile"', proc.stdout)

    def test_removed_toolbelt_make_targets_not_advertised_in_first_party_docs(self):
        stale_target_re = re.compile(
            r"deploy-toolbelt|run-toolbelt|install-toolbelt|toolbelt-legacy|"
            r"make install\b|make autolaunch"
        )
        docs = [
            ROOT / "AGENTS.md",
            ROOT / "README.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "CLAUDE.md",
            *sorted((ROOT / "docs").rglob("*.md")),
        ]

        for path in docs:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(
                    stale_target_re.search(path.read_text(encoding="utf-8")),
                    f"{path.relative_to(ROOT)} advertises a removed Toolbelt Makefile target",
                )

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
