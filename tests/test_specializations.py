import importlib
import os
import tempfile
import unittest
from pathlib import Path


class SpecializationManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "repo" / "subdir"
        self.project.mkdir(parents=True)
        self.project_specs = self.root / "repo" / ".torque" / "specializations"
        self.project_specs.mkdir(parents=True)
        self.user_home = self.root / "home"
        self.user_specs = self.user_home / ".torque" / "specializations"
        self.user_specs.mkdir(parents=True)

        self.prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.user_home)
        self.addCleanup(self._restore_home)

        self.spec_mod = importlib.import_module("torque.specializations")
        self.spec_mod = importlib.reload(self.spec_mod)
        self.mgr = self.spec_mod.SpecializationManager()

    def _restore_home(self):
        if self.prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.prev_home

    def test_round_trip_preamble_and_priorities(self):
        path = self.mgr.save_specialization(
            "ui-ux",
            {
                "name": "ui-ux",
                "description": "UI-focused",
                "preamble": "You are a UI/UX engineer.",
                "priorities": ["rerender hygiene", "accessibility"],
            },
            scope="user",
            base_dir=str(self.project),
        )

        self.assertEqual(path, str(self.user_specs / "ui-ux.yaml"))
        raw = (self.user_specs / "ui-ux.yaml").read_text(encoding="utf-8")
        self.assertIn("preamble: |", raw)
        self.assertIn("- rerender hygiene", raw)

        loaded = self.mgr.get_specialization("ui-ux", base_dir=str(self.project))
        self.assertEqual(loaded["preamble"], "You are a UI/UX engineer.")
        self.assertEqual(
            loaded["priorities"], ["rerender hygiene", "accessibility"])
        self.assertEqual(loaded["description"], "UI-focused")

    def test_project_shadows_user(self):
        (self.user_specs / "security.yaml").write_text(
            "name: security\ndescription: global\npreamble: |\n  Global.\n",
            encoding="utf-8",
        )
        (self.project_specs / "security.yaml").write_text(
            "name: security\ndescription: project\npreamble: |\n  Project.\n",
            encoding="utf-8",
        )

        loaded = self.mgr.get_specialization(
            "security", base_dir=str(self.project))
        self.assertEqual(loaded["description"], "project")

        listed = self.mgr.list_specializations(base_dir=str(self.project))
        security_entries = [item for item in listed if item["name"] == "security"]
        self.assertEqual(len(security_entries), 2)
        project_entry = next(e for e in security_entries if not e["global"])
        self.assertEqual(project_entry["description"], "project")
        self.assertEqual(project_entry["path"],
                         str(self.project_specs / "security.yaml"))

    def test_canonical_project_names_excludes_user_specializations(self):
        (self.user_specs / "local-only.yaml").write_text(
            "name: local-only\npreamble: local\n",
            encoding="utf-8",
        )
        (self.project_specs / "ui-ux.yaml").write_text(
            "name: ui-ux\npreamble: project\n",
            encoding="utf-8",
        )

        self.assertEqual(
            self.mgr.canonical_project_names(base_dir=str(self.project)),
            ["ui-ux"],
        )

    def test_delete_removes_file(self):
        self.mgr.save_specialization(
            "events",
            {"name": "events", "preamble": "Events focus."},
            scope="user",
            base_dir=str(self.project),
        )
        path = self.user_specs / "events.yaml"
        self.assertTrue(path.exists())
        deleted = self.mgr.delete_specialization(
            "events", scope="user", base_dir=str(self.project))
        self.assertTrue(deleted)
        self.assertFalse(path.exists())

    def test_save_rejects_unknown_fields(self):
        with self.assertRaises(ValueError):
            self.mgr.save_specialization(
                "bad",
                {"name": "bad", "surprise": "field"},
                scope="user",
                base_dir=str(self.project),
            )

    def test_render_preamble_shapes(self):
        render = self.spec_mod.SpecializationManager.render_preamble
        self.assertEqual(render({}), "")
        self.assertEqual(render({"preamble": "P."}), "P.")
        self.assertEqual(
            render({"priorities": ["a", "b"]}),
            "Priorities:\n- a\n- b",
        )
        self.assertEqual(
            render({
                "preamble": "P.",
                "priorities": ["a", "b"],
            }),
            "P.\n\nPriorities:\n- a\n- b",
        )

    def test_render_engineer_preamble_concatenates_ordered(self):
        self.mgr.save_specialization(
            "ui-ux",
            {
                "name": "ui-ux",
                "preamble": "UX first.",
                "priorities": ["rerender hygiene"],
            },
            scope="user",
            base_dir=str(self.project),
        )
        self.mgr.save_specialization(
            "security-focus",
            {
                "name": "security-focus",
                "preamble": "Security second.",
                "priorities": ["threat modeling"],
            },
            scope="user",
            base_dir=str(self.project),
        )

        rendered = self.mgr.render_engineer_preamble(
            ["ui-ux", "security-focus"], base_dir=str(self.project))

        self.assertIn(
            "Specializations: ui-ux (primary), security-focus", rendered)
        self.assertIn("UX first.", rendered)
        self.assertIn("Security second.", rendered)
        self.assertLess(
            rendered.index("UX first."),
            rendered.index("Security second."),
        )

    def test_render_engineer_preamble_skips_missing(self):
        self.mgr.save_specialization(
            "ui-ux",
            {"name": "ui-ux", "preamble": "UX first."},
            scope="user",
            base_dir=str(self.project),
        )
        with self.assertLogs("torque", level="WARNING"):
            rendered = self.mgr.render_engineer_preamble(
                ["ui-ux", "not-a-thing"], base_dir=str(self.project))
        self.assertIn("Specializations: ui-ux (primary)", rendered)
        self.assertIn("UX first.", rendered)
        self.assertNotIn("not-a-thing", rendered)

    def test_render_engineer_preamble_empty_when_none_resolve(self):
        rendered = self.mgr.render_engineer_preamble(
            [], base_dir=str(self.project))
        self.assertEqual(rendered, "")
        rendered = self.mgr.render_engineer_preamble(
            ["missing"], base_dir=str(self.project))
        self.assertEqual(rendered, "")


if __name__ == "__main__":
    unittest.main()
