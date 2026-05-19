import unittest
from pathlib import Path

from torque.roles import RoleManager
from torque.specializations import SpecializationManager


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProjectTaxonomyTests(unittest.TestCase):
    EXPECTED_ROLES = {
        "ui-worker",
        "orchestration-worker",
        "runtime-worker",
        "desktop-worker",
        "release-worker",
        "prompts-worker",
        "quality-worker",
    }
    EXPECTED_SPECIALIZATIONS = {
        "ui-ux",
        "orchestration-core",
        "runtime-pty",
        "desktop-shell",
        "worktree-release",
        "prompts-config",
        "quality-observability",
    }
    ROLE_MAPPING = {
        "ui-ux": "ui-worker",
        "orchestration-core": "orchestration-worker",
        "runtime-pty": "runtime-worker",
        "desktop-shell": "desktop-worker",
        "worktree-release": "release-worker",
        "prompts-config": "prompts-worker",
        "quality-observability": "quality-worker",
    }

    def test_project_roles_are_discoverable_and_minimal(self):
        roles = RoleManager().list_roles(str(REPO_ROOT))
        project_roles = {
            item["name"]: item
            for item in roles
            if not item.get("global") and item["name"] in self.EXPECTED_ROLES
        }

        self.assertEqual(set(project_roles), self.EXPECTED_ROLES)
        for name, item in project_roles.items():
            self.assertEqual(
                Path(item["path"]), REPO_ROOT / ".torque" / "roles" / f"{name}.yaml"
            )
            loaded = RoleManager().load_role(name, base_dir=str(REPO_ROOT))
            self.assertEqual(loaded["name"], name)
            self.assertIn("preamble", loaded)
            self.assertIn("priorities", loaded)
            self.assertNotIn("provider", loaded)
            self.assertNotIn("command", loaded)
            self.assertNotIn("worktree", loaded)

    def test_project_specializations_are_discoverable(self):
        specs = SpecializationManager().list_specializations(str(REPO_ROOT))
        project_specs = {
            item["name"]: item
            for item in specs
            if not item.get("global") and item["name"] in self.EXPECTED_SPECIALIZATIONS
        }

        self.assertEqual(set(project_specs), self.EXPECTED_SPECIALIZATIONS)
        for name, item in project_specs.items():
            self.assertEqual(
                Path(item["path"]),
                REPO_ROOT / ".torque" / "specializations" / f"{name}.yaml",
            )
            loaded = SpecializationManager().get_specialization(
                name, base_dir=str(REPO_ROOT)
            )
            self.assertEqual(loaded["name"], name)
            self.assertIn("preamble", loaded)
            self.assertIn("priorities", loaded)

    def test_docs_reference_full_mapping(self):
        ref = (REPO_ROOT / "docs" / "reference" / "specializations.md").read_text(
            encoding="utf-8"
        )
        for spec, role in self.ROLE_MAPPING.items():
            self.assertIn(spec, ref)
            self.assertIn(role, ref)


if __name__ == "__main__":
    unittest.main()
