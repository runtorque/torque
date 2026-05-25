import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BootContextBudgetDocsTest(unittest.TestCase):
    def test_claude_md_stays_terse_and_points_to_moved_reference_docs(self):
        claude = (ROOT / "CLAUDE.md").read_text()

        self.assertLessEqual(
            len(claude),
            16_000,
            "CLAUDE.md is injected into agent boot; keep reference detail in docs/.",
        )
        for moved_heading in (
            "## Claude Code hooks gotchas",
            "## Install location",
        ):
            self.assertNotIn(moved_heading, claude)
        self.assertNotIn("For manual runtime testing:", claude)

        for reference in (
            "docs/reference/architecture.md",
            "docs/reference/hooks-gotchas.md",
            "docs/reference/install-locations.md",
            "docs/operate/manual-testing.md",
        ):
            self.assertIn(reference, claude)
            self.assertTrue((ROOT / reference).exists(), reference)

    def test_mkdocs_nav_includes_moved_reference_docs(self):
        nav = (ROOT / "mkdocs.yml").read_text()
        for reference in (
            "reference/architecture.md",
            "reference/hooks-gotchas.md",
            "reference/install-locations.md",
            "operate/manual-testing.md",
        ):
            self.assertIn(reference, nav)


if __name__ == "__main__":
    unittest.main()
