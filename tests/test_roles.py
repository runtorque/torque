import importlib
import os
import tempfile
import unittest
from pathlib import Path


class RoleManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)
        self.project_roles = self.root / "repo" / ".torque" / "roles"
        self.project_roles.mkdir(parents=True)
        self.project_templates = self.root / "repo" / ".torque" / "agents"
        self.project_templates.mkdir(parents=True)
        self.user_home = self.root / "home"
        self.user_roles = self.user_home / ".torque" / "roles"
        self.user_roles.mkdir(parents=True)
        self.user_templates = self.user_home / ".torque" / "agents"
        self.user_templates.mkdir(parents=True)
        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.user_home)
        self.addCleanup(self._restore_home)

        self.roles_mod = importlib.import_module("torque.roles")
        self.roles_mod = importlib.reload(self.roles_mod)
        self.mgr = self.roles_mod.RoleManager()

    def _restore_home(self):
        if self.prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.prev_home

    def test_user_role_round_trips_preamble_and_priorities(self):
        path = self.mgr.save_role(
            "demo",
            {
                "name": "demo",
                "description": "Review carefully",
                "preamble": "Be careful.\nShip small.",
                "priorities": ["ship small", "test first"],
            },
            scope="user",
            base_dir=str(self.project),
        )

        self.assertEqual(path, str(self.user_roles / "demo.yaml"))
        raw = (self.user_roles / "demo.yaml").read_text(encoding="utf-8")
        self.assertIn("preamble: |", raw)
        self.assertIn("- ship small", raw)
        loaded = self.mgr.load_role("demo", base_dir=str(self.project))
        self.assertEqual(loaded["preamble"], "Be careful.\nShip small.")
        self.assertEqual(loaded["priorities"], ["ship small", "test first"])

        listed = self.mgr.list_roles(str(self.project))
        demo = next(item for item in listed if item["name"] == "demo")
        self.assertEqual(demo["preamble"], "Be careful.\nShip small.")
        self.assertEqual(demo["priorities"], ["ship small", "test first"])
        self.assertEqual(demo["path"], str(self.user_roles / "demo.yaml"))

    def test_legacy_template_is_ignored_with_warning(self):
        legacy_path = self.user_templates / "bar.yaml"
        legacy_path.write_text(
            "name: bar\nprovider: codex\nmodel: gpt-5\n",
            encoding="utf-8",
        )

        with self.assertLogs("torque", level="WARNING") as logs:
            loaded = self.mgr.load_role("bar", base_dir=str(self.project))

        self.assertIsNone(loaded)
        self.assertEqual(
            logs.output,
            [
                "WARNING:torque:legacy template file at ~/.torque/agents/bar.yaml "
                "is ignored; move it to ~/.torque/roles/ to use as a role"
            ],
        )
        self.assertEqual(
            [item["name"] for item in self.mgr.list_roles(str(self.project))],
            [],
        )

    def test_matching_role_suppresses_legacy_warning_and_uses_role(self):
        (self.user_roles / "foo.yaml").write_text(
            "name: foo\ndescription: Role version\npreamble: |\n  Be careful.\n",
            encoding="utf-8",
        )
        (self.user_templates / "foo.yaml").write_text(
            "name: foo\ndescription: Legacy version\n",
            encoding="utf-8",
        )

        with self.assertNoLogs("torque", level="WARNING"):
            listed = self.mgr.list_roles(str(self.project))
            loaded = self.mgr.load_role("foo", base_dir=str(self.project))

        self.assertEqual(
            [item["name"] for item in listed if item["name"] == "foo"],
            ["foo"],
        )
        foo = next(item for item in listed if item["name"] == "foo")
        self.assertFalse(foo["legacy"])
        self.assertEqual(foo["description"], "Role version")
        self.assertEqual(loaded["description"], "Role version")

    def test_project_legacy_no_longer_overrides_user_role(self):
        (self.project_templates / "foo.yaml").write_text(
            "name: foo\ndescription: project legacy\n",
            encoding="utf-8",
        )
        (self.user_roles / "foo.yaml").write_text(
            "name: foo\ndescription: user role\n",
            encoding="utf-8",
        )

        with self.assertLogs("torque", level="WARNING") as logs:
            loaded = self.mgr.load_role("foo", base_dir=str(self.project))
            listed = self.mgr.list_roles(str(self.project))

        self.assertEqual(loaded["description"], "user role")
        matches = [item for item in listed if item["name"] == "foo"]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["global"])
        self.assertFalse(matches[0]["shadowed"])
        self.assertEqual(matches[0]["description"], "user role")
        self.assertEqual(
            logs.output,
            [
                f"WARNING:torque:legacy template file at {self.project_templates}/foo.yaml "
                f"is ignored; move it to {self.project / '.torque' / 'roles'}/ to use as a role"
            ],
        )

    def test_list_roles_returns_single_user_entry_without_project_shadow(self):
        project_in_home = self.user_home / "workspace" / "repo" / "subdir"
        project_in_home.mkdir(parents=True)
        (self.user_roles / "solo.yaml").write_text(
            "name: solo\ndescription: User role\n",
            encoding="utf-8",
        )

        listed = self.mgr.list_roles(str(project_in_home))

        matches = [item for item in listed if item["name"] == "solo"]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["global"])
        self.assertFalse(matches[0]["shadowed"])
        self.assertEqual(matches[0]["dir"], str(self.user_roles))

    def test_save_role_writes_to_roles_directory_even_when_legacy_exists(self):
        legacy_path = self.user_templates / "bar.yaml"
        legacy_path.write_text("name: bar\ndescription: Legacy\n", encoding="utf-8")

        with self.assertLogs("torque", level="WARNING") as logs:
            path = self.mgr.save_role(
                "bar",
                {
                    "name": "bar",
                    "description": "Role version",
                    "preamble": "Be careful.",
                },
                scope="user",
                base_dir=str(self.project),
            )

        self.assertEqual(path, str(self.user_roles / "bar.yaml"))
        self.assertTrue((self.user_roles / "bar.yaml").is_file())
        self.assertEqual(
            legacy_path.read_text(encoding="utf-8"),
            "name: bar\ndescription: Legacy\n",
        )
        self.assertEqual(
            logs.output,
            [
                "WARNING:torque:legacy template file at ~/.torque/agents/bar.yaml "
                "is ignored; move it to ~/.torque/roles/ to use as a role"
            ],
        )

    def test_delete_template_does_not_remove_legacy_only_entry(self):
        legacy_path = self.user_templates / "legacy.yaml"
        legacy_path.write_text("name: legacy\ndescription: Legacy\n", encoding="utf-8")

        with self.assertLogs("torque", level="WARNING") as logs:
            deleted = self.mgr.delete_template(
                "legacy",
                scope="user",
                base_dir=str(self.project),
            )

        self.assertFalse(deleted)
        self.assertTrue(legacy_path.exists())
        self.assertEqual(
            logs.output,
            [
                "WARNING:torque:legacy template file at ~/.torque/agents/legacy.yaml "
                "is ignored; move it to ~/.torque/roles/ to use as a role"
            ],
        )

    def test_delete_role_does_not_remove_legacy_only_entry(self):
        legacy_path = self.user_templates / "legacy.yaml"
        legacy_path.write_text("name: legacy\ndescription: Legacy\n", encoding="utf-8")

        with self.assertLogs("torque", level="WARNING") as logs:
            deleted = self.mgr.delete_role(
                "legacy",
                scope="user",
                base_dir=str(self.project),
            )

        self.assertFalse(deleted)
        self.assertTrue(legacy_path.exists())
        self.assertEqual(
            logs.output,
            [
                "WARNING:torque:legacy template file at ~/.torque/agents/legacy.yaml "
                "is ignored; move it to ~/.torque/roles/ to use as a role"
            ],
        )

    def test_render_preamble_formats_all_supported_shapes(self):
        render = self.mgr.render_preamble

        self.assertEqual(render({}), "")
        self.assertEqual(render({"preamble": "Be careful."}), "Be careful.")
        self.assertEqual(
            render({"priorities": ["ship small", "test first"]}),
            "Priorities:\n- ship small\n- test first",
        )
        self.assertEqual(
            render({
                "preamble": "Be careful.",
                "priorities": ["ship small", "test first"],
            }),
            "Be careful.\n\nPriorities:\n- ship small\n- test first",
        )


if __name__ == "__main__":
    unittest.main()
