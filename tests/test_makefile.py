import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MakefileInstallTests(unittest.TestCase):
    def _footer_command(self, source):
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"

    def _run_footer_target(self, target, result_dir, source, *, env=None, python=None, **variables):
        command_key = "TEST_EE_COMMAND" if target == "test-ee" else "TEST_COMMAND"
        arguments = [
            "make", "-s", target,
            f"TEST_PYTHON={python or sys.executable}",
            f"TEST_RESULT_DIR={result_dir}",
            f"{command_key}={self._footer_command(source)}",
            *[f"{key}={value}" for key, value in variables.items()],
        ]
        return subprocess.run(
            arguments, cwd=ROOT, text=True, capture_output=True, env=env,
        )

    def _footer_records(self, result_dir):
        return sorted(result_dir.glob("*.json"))

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

    def test_test_target_preserves_user_site_and_isolates_fixture_profile(self):
        """Test isolation must retain user-site dependencies while moving logs."""
        if not shutil.which("make"):
            self.skipTest("make is not available")

        with tempfile.TemporaryDirectory() as td:
            operator_home = Path(td) / "operator-home"
            live_log = operator_home / ".torque" / "profiles" / "default" / "torque.log"
            live_log.parent.mkdir(parents=True)
            live_log.write_text("operator log sentinel\n", encoding="utf-8")

            fixture = """import os
for name in tuple(os.environ):
    if name.startswith("TORQUE_"):
        del os.environ[name]
import make_test_user_site_fixture
assert make_test_user_site_fixture.VALUE == "available"
from torque import config
config.log.info("test fixture log sentinel")
for handler in config.log.handlers:
    handler.flush()
print(config.LOG_FILE)
"""
            env = os.environ.copy()
            env.pop("PYTHONUSERBASE", None)
            env.update({
                "HOME": str(operator_home),
                "TORQUE_DATA_DIR": str(live_log.parent),
                "TORQUE_PROFILE": "default",
            })
            user_site = Path(subprocess.check_output(
                [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
                text=True,
                env=env,
            ).strip())
            user_site.mkdir(parents=True)
            (user_site / "make_test_user_site_fixture.py").write_text(
                'VALUE = "available"\n', encoding="utf-8"
            )
            fixture_b64 = base64.b64encode(fixture.encode("utf-8")).decode("ascii")
            fixture_command = (
                f'{sys.executable} -c "import base64; exec(compile('
                f'base64.b64decode(\'{fixture_b64}\'), \'<fixture>\', \'exec\'))"'
            )
            proc = subprocess.run(
                [
                    "make",
                    "test",
                    f"TEST_PYTHON={sys.executable}",
                    f"TEST_COMMAND={fixture_command}",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )

            self.assertEqual(live_log.read_text(encoding="utf-8"), "operator log sentinel\n")
            self.assertNotIn(str(live_log), proc.stdout)
            self.assertIn("/torque-test.", proc.stdout)
            self.assertIn("/.torque/profiles/default/torque.log", proc.stdout)

    def test_test_targets_allocate_disposable_home_and_data_dirs(self):
        for target in ("test", "test-ee"):
            with self.subTest(target=target):
                proc = self._run_make_dry(target)
                self.assertIn('scratch_root=$(mktemp -d "${TMPDIR:-/tmp}/torque-test.XXXXXX")', proc.stdout)
                self.assertIn('HOME="$scratch_root/home"', proc.stdout)
                self.assertIn('TORQUE_DATA_DIR="$scratch_root/profile"', proc.stdout)
                self.assertIn('TORQUE_PROFILE="test"', proc.stdout)
                self.assertIn("TEST_PYTHON ?= python3", (ROOT / "Makefile").read_text(encoding="utf-8"))
                self.assertIn("test_user_base=$(python3 -c 'import site; print(site.USER_BASE)')", proc.stdout)
                self.assertIn('PYTHONUSERBASE="$test_user_base"', proc.stdout)
                self.assertIn('trap \'rm -rf \"$scratch_root\"\' EXIT HUP INT TERM', proc.stdout)
                self.assertIn("scripts/test_result_footer.py prepare", proc.stdout)
                self.assertIn("scripts/test_result_footer.py finalize", proc.stdout)
                self.assertIn('suite_rc=$?', proc.stdout)
                self.assertIn('TEST_RESULT_DIR ?= $(CURDIR)/.torque/test-results', (ROOT / "Makefile").read_text(encoding="utf-8"))

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

    def test_test_footers_preserve_streams_and_suite_exit_status_for_both_targets(self):
        for target in ("test", "test-ee"):
            with self.subTest(target=target):
                with tempfile.TemporaryDirectory() as td:
                    result_dir = Path(td) / "results"
                    passing_source = "import sys; print(\"suite stdout, quoted 'value'\"); print('suite stderr', file=sys.stderr)"
                    passing_command = self._footer_command(passing_source)
                    passing = self._run_footer_target(
                        target, result_dir, passing_source,
                    )
                    self.assertEqual(passing.returncode, 0, passing.stderr)
                    self.assertIn("suite stdout, quoted 'value'", passing.stdout)
                    self.assertIn("suite stderr", passing.stderr)
                    records = self._footer_records(result_dir)
                    self.assertEqual(len(records), 1)
                    record = json.loads(records[0].read_text(encoding="utf-8"))
                    self.assertEqual(record["target"], target)
                    self.assertEqual(record["exit_code"], 0)
                    self.assertEqual(record["command"], passing_command)
                    self.assertIsNone(record["totals"])
                    self.assertEqual(record["totals_source"], "stdout")
                    self.assertIn("change process, TTY, and stream semantics", record["totals_parse_reason"])

                    failing = self._run_footer_target(
                        target, result_dir,
                        "import sys; print('intentional unique failure'); sys.exit(37)",
                    )
                    # GNU make maps a failed recipe to its own non-zero exit,
                    # while the immutable footer preserves the suite's 37.
                    self.assertNotEqual(failing.returncode, 0, failing.stderr)
                    records = self._footer_records(result_dir)
                    self.assertEqual(len(records), 2)
                    self.assertEqual(
                        json.loads(records[-1].read_text(encoding="utf-8"))["exit_code"], 37,
                    )

    def test_footer_survives_discarded_output_and_torque_sanitizer(self):
        with tempfile.TemporaryDirectory() as td:
            result_dir = Path(td) / "selected-by-non-torque-make-variable"
            env = os.environ.copy()
            env["TORQUE_CALLER_SENTINEL"] = "must-not-reach-suite"
            command = self._footer_command(
                "import os; assert os.getenv('TORQUE_CALLER_SENTINEL') is None"
            )
            proc = subprocess.run(
                [
                    "make", "-s", "test", f"TEST_PYTHON={sys.executable}",
                    f"TEST_RESULT_DIR={result_dir}", f"TEST_COMMAND={command}",
                ],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.assertEqual(proc.returncode, 0)
            records = self._footer_records(result_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(
                json.loads(records[0].read_text(encoding="utf-8"))["output_truncated"], "unknown",
            )

    def test_footer_records_are_immutable_and_lint_failure_creates_no_stale_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            result_dir = Path(td) / "records"
            passing = self._run_footer_target(result_dir=result_dir, target="test", source="pass")
            self.assertEqual(passing.returncode, 0, passing.stderr)
            first = self._footer_records(result_dir)
            self.assertEqual(len(first), 1)
            first_bytes = first[0].read_bytes()

            second = self._run_footer_target(result_dir=result_dir, target="test", source="pass")
            self.assertEqual(second.returncode, 0, second.stderr)
            records = self._footer_records(result_dir)
            self.assertEqual(len(records), 2)
            self.assertEqual(first[0].read_bytes(), first_bytes)
            self.assertFalse(any(path.name in {"latest.json", "current.json"} for path in result_dir.iterdir()))

            lint_env = os.environ.copy()
            lint_env["PATH"] = ""
            lint = subprocess.run(
                [
                    shutil.which("make") or "make", "-s", "test",
                    f"TEST_PYTHON={sys.executable}", f"TEST_RESULT_DIR={result_dir}",
                ],
                cwd=ROOT, text=True, capture_output=True, env=lint_env,
            )
            self.assertNotEqual(lint.returncode, 0)
            self.assertEqual(self._footer_records(result_dir), records)
            self.assertEqual(first[0].read_bytes(), first_bytes)

    def test_footer_provenance_distinguishes_clean_and_dirty_worktrees(self):
        helper = ROOT / "scripts" / "test_result_footer.py"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / "scripts").mkdir()
            copied_helper = repo / "scripts" / helper.name
            copied_helper.write_text(helper.read_text(encoding="utf-8"), encoding="utf-8")
            (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"],
                cwd=repo, check=True,
            )
            result_dir = repo / "results"

            def prepare_and_finalize(name):
                invocation = repo / f"{name}.invocation"
                env = os.environ | {"COMMAND": "synthetic command"}
                subprocess.run(
                    [sys.executable, str(copied_helper), "prepare", "--target", "test",
                     "--command-env", "COMMAND",
                     "--result-dir", str(result_dir), "--invocation", str(invocation)],
                    cwd=repo, env=env, check=True,
                )
                subprocess.run(
                    [sys.executable, str(copied_helper), "finalize", "--invocation", str(invocation), "--exit-code", "0"],
                    cwd=repo, check=True,
                )

            prepare_and_finalize("clean")
            (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            prepare_and_finalize("dirty")
            records = [json.loads(path.read_text(encoding="utf-8")) for path in self._footer_records(result_dir)]
            self.assertEqual({record["dirty"] for record in records}, {False, True})
            expected_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertEqual({record["sha"] for record in records}, {expected_sha})
            self.assertTrue(all(record["interpreter"]["path"] for record in records))

    def test_footer_records_distinct_real_interpreters(self):
        older_python = shutil.which("python3", path="/usr/bin")
        if not older_python or os.path.realpath(older_python) == os.path.realpath(sys.executable):
            self.skipTest("two distinct real Python interpreters are unavailable")
        helper = ROOT / "scripts" / "test_result_footer.py"
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (repo / "scripts").mkdir()
            copied_helper = repo / "scripts" / helper.name
            copied_helper.write_text(helper.read_text(encoding="utf-8"), encoding="utf-8")
            (repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "fixture"],
                cwd=repo, check=True,
            )
            result_dir = repo / "results"
            for index, interpreter in enumerate((sys.executable, older_python)):
                invocation = repo / f"interpreter-{index}.invocation"
                env = os.environ | {"COMMAND": "synthetic command", "TEST_PYTHON": interpreter}
                subprocess.run(
                    [interpreter, str(copied_helper), "prepare", "--target", "test",
                     "--command-env", "COMMAND", "--result-dir", str(result_dir),
                     "--invocation", str(invocation)],
                    cwd=repo, env=env, check=True,
                )
                subprocess.run(
                    [interpreter, str(copied_helper), "finalize", "--invocation", str(invocation), "--exit-code", "0"],
                    cwd=repo, check=True,
                )
            records = [json.loads(path.read_text(encoding="utf-8")) for path in self._footer_records(result_dir)]
            self.assertEqual(len(records), 2)
            self.assertEqual(len({record["interpreter"]["path"] for record in records}), 2)
            self.assertEqual(len({record["interpreter"]["version"] for record in records}), 2)


if __name__ == "__main__":
    unittest.main()
