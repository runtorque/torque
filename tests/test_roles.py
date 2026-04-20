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
        self.project_roles = self.root / "repo" / ".loom" / "roles"
        self.project_roles.mkdir(parents=True)
        self.project_templates = self.root / "repo" / ".loom" / "agents"
        self.project_templates.mkdir(parents=True)
        self.user_home = self.root / "home"
        self.user_roles = self.user_home / ".loom" / "roles"
        self.user_roles.mkdir(parents=True)
        self.user_templates = self.user_home / ".loom" / "agents"
        self.user_templates.mkdir(parents=True)
        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.user_home)
        self.addCleanup(self._restore_home)

        self.roles_mod = importlib.import_module("loom.roles")
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
        raw = (self.user_roles / "demo.yaml").read_text()
        self.assertIn("preamble: |", raw)
        self.assertIn("- ship small", raw)
        loaded = self.mgr.load_role("demo", base_dir=str(self.project))
        self.assertEqual(loaded["preamble"], "Be careful.\nShip small.")
        self.assertEqual(loaded["priorities"], ["ship small", "test first"])

        listed = self.mgr.list_roles(str(self.project))
        demo = next(item for item in listed if item["name"] == "demo")
        self.assertEqual(demo["preamble"], "Be careful.\nShip small.")
        self.assertEqual(demo["priorities"], ["ship small", "test first"])

    def test_role_shadows_legacy_template_with_warning(self):
        (self.user_roles / "foo.yaml").write_text(
            "name: foo\ndescription: Role version\npreamble: |\n  Be careful.\n"
        )
        (self.user_templates / "foo.yaml").write_text(
            "name: foo\ndescription: Legacy version\n"
        )

        with self.assertLogs("loom", level="WARNING") as logs:
            listed = self.mgr.list_roles(str(self.project))

        self.assertEqual(
            [item["name"] for item in listed if item["name"] == "foo"],
            ["foo"],
        )
        foo = next(item for item in listed if item["name"] == "foo")
        self.assertFalse(foo["legacy"])
        self.assertEqual(foo["description"], "Role version")
        self.assertEqual(
            logs.output,
            ["WARNING:loom:role 'foo' shadows legacy template"],
        )

    def test_legacy_template_still_loads_with_empty_preamble_defaults(self):
        (self.user_templates / "bar.yaml").write_text(
            "name: bar\nprovider: codex\nmodel: gpt-5\n"
        )

        loaded = self.mgr.load_role("bar", base_dir=str(self.project))

        self.assertEqual(loaded["provider"], "codex")
        self.assertEqual(loaded["model"], "gpt-5")
        self.assertEqual(loaded.get("preamble", ""), "")
        self.assertEqual(loaded.get("priorities", []), [])

        listed = self.mgr.list_roles(str(self.project))
        bar = next(item for item in listed if item["name"] == "bar")
        self.assertTrue(bar["legacy"])
        self.assertEqual(bar["preamble"], "")
        self.assertEqual(bar["priorities"], [])

    def test_project_legacy_overrides_user_role_for_same_name(self):
        (self.project_templates / "foo.yaml").write_text(
            "name: foo\ndescription: project legacy\n"
        )
        (self.user_roles / "foo.yaml").write_text(
            "name: foo\ndescription: user role\n"
        )

        with self.assertNoLogs("loom", level="WARNING"):
            loaded = self.mgr.load_role("foo", base_dir=str(self.project))
            listed = self.mgr.list_roles(str(self.project))

        self.assertEqual(loaded["description"], "project legacy")
        matches = [item for item in listed if item["name"] == "foo"]
        self.assertEqual(len(matches), 2)
        project_entry = next(item for item in matches if not item["global"])
        user_entry = next(item for item in matches if item["global"])
        self.assertTrue(project_entry["legacy"])
        self.assertEqual(project_entry["description"], "project legacy")
        self.assertFalse(project_entry["shadowed"])
        self.assertFalse(user_entry["legacy"])
        self.assertEqual(user_entry["description"], "user role")
        self.assertTrue(user_entry["shadowed"])

    def test_save_role_writes_to_roles_directory_even_for_legacy_origin(self):
        legacy_path = self.user_templates / "bar.yaml"
        legacy_path.write_text("name: bar\ndescription: Legacy\n")

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
        self.assertEqual(legacy_path.read_text(), "name: bar\ndescription: Legacy\n")

    def test_delete_template_removes_legacy_only_entry(self):
        legacy_path = self.user_templates / "legacy.yaml"
        legacy_path.write_text("name: legacy\ndescription: Legacy\n")

        deleted = self.mgr.delete_template(
            "legacy",
            scope="user",
            base_dir=str(self.project),
        )

        self.assertTrue(deleted)
        self.assertFalse(legacy_path.exists())

    def test_delete_role_removes_legacy_only_entry(self):
        legacy_path = self.user_templates / "legacy.yaml"
        legacy_path.write_text("name: legacy\ndescription: Legacy\n")

        listed = self.mgr.list_roles(str(self.project))
        legacy = next(item for item in listed if item["name"] == "legacy")
        self.assertTrue(legacy["legacy"])

        deleted = self.mgr.delete_role(
            "legacy",
            scope="user",
            base_dir=str(self.project),
        )

        self.assertTrue(deleted)
        self.assertFalse(legacy_path.exists())

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
