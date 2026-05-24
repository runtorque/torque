import json
import tomllib
import unittest
from pathlib import Path

try:
    from ee_gate import ee_skip_reason, ee_tests_enabled
except ModuleNotFoundError:  # Support `python -m unittest tests.<module>`.
    from tests.ee_gate import ee_skip_reason, ee_tests_enabled

_EE_REQUIRED_PATHS = [
    "ee/LICENSE",
    "ee/frontend/README.md",
    "ee/python/README.md",
    "ee/relay/README.md",
]
_EE_TESTS_ENABLED = ee_tests_enabled(_EE_REQUIRED_PATHS)
_EE_SKIP_REASON = ee_skip_reason(_EE_REQUIRED_PATHS)

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(_EE_TESTS_ENABLED, _EE_SKIP_REASON)
class EeLicenseBoundaryTests(unittest.TestCase):
    def test_root_license_explicitly_excludes_ee(self):
        text = (ROOT / "LICENSE").read_text()
        self.assertIn("MIT License", text)
        self.assertIn("except for the\n`ee/` directory", text)
        self.assertIn("not covered by this license", text)
        self.assertIn("ee/LICENSE", text)

    def test_ee_has_restrictive_all_rights_reserved_license(self):
        text = (ROOT / "ee" / "LICENSE").read_text()
        self.assertIn("All rights reserved", text)
        self.assertIn("not covered by the repository root MIT License", text)
        self.assertIn("No license or rights are granted", text)
        self.assertIn("distribute", text)
        self.assertIn("separate written agreement", text)

    def test_readmes_point_to_ee_license_boundary(self):
        root_readme = (ROOT / "README.md").read_text()
        self.assertIn("except for `ee/`", root_readme)
        self.assertIn("[ee/LICENSE](ee/LICENSE)", root_readme)

        ee_readme = (ROOT / "ee" / "README.md").read_text()
        self.assertIn("not covered by the repository root MIT License", ee_readme)
        self.assertIn("[LICENSE](LICENSE)", ee_readme)

        for rel in (
            "ee/frontend/README.md",
            "ee/python/README.md",
            "ee/relay/README.md",
        ):
            with self.subTest(path=rel):
                text = (ROOT / rel).read_text()
                self.assertIn("License boundary:", text)
                self.assertIn("[../LICENSE](../LICENSE)", text)

    def test_ee_package_metadata_is_not_open_source_licensed(self):
        relay_package = json.loads((ROOT / "ee" / "relay" / "package.json").read_text())
        self.assertTrue(relay_package["private"])
        self.assertEqual(relay_package["license"], "UNLICENSED")

        pyproject = tomllib.loads((ROOT / "ee" / "python" / "pyproject.toml").read_text())
        self.assertEqual(pyproject["project"]["license"]["text"], "Proprietary")


if __name__ == "__main__":
    unittest.main()
