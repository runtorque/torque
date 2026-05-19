import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINT = ROOT / "scripts" / "lint_tauri_permissions.py"


class TauriPermissionsLintTests(unittest.TestCase):
    def make_repo(self, repo, capability_permissions=None):
        if capability_permissions is None:
            capability_permissions = ["allow-foo-bar"]
        (repo / "src-tauri" / "src").mkdir(parents=True)
        (repo / "src-tauri" / "permissions").mkdir(parents=True)
        (repo / "src-tauri" / "src" / "main.rs").write_text(
            """
fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![commands::foo_bar]);
}
""",
            encoding="utf-8",
        )
        (repo / "src-tauri" / "permissions" / "default.toml").write_text(
            """
[[permission]]
identifier = "allow-foo-bar"
description = "Allow foo_bar."
commands.allow = ["foo_bar"]
""",
            encoding="utf-8",
        )
        if capability_permissions is not False:
            (repo / "src-tauri" / "capabilities").mkdir(parents=True)
            (repo / "src-tauri" / "capabilities" / "default.json").write_text(
                json.dumps({"identifier": "default", "permissions": capability_permissions}),
                encoding="utf-8",
            )

    def run_lint(self, repo):
        return subprocess.run(
            [sys.executable, str(LINT), str(repo)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_passes_when_permission_is_defined_and_enabled_by_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.make_repo(repo)
            proc = self.run_lint(repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 command handlers checked", proc.stdout)

    def test_fails_when_permission_exists_but_capability_reference_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.make_repo(repo, capability_permissions=["allow-something-else"])
            proc = self.run_lint(repo)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Missing Tauri capability references", proc.stderr)
        self.assertIn('expected "allow-foo-bar"', proc.stderr)


if __name__ == "__main__":
    unittest.main()
