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

    def test_agents_md_tracks_claude_guidance_without_dead_plan_links(self):
        agents = (ROOT / "AGENTS.md").read_text()

        self.assertIn("Treat `CLAUDE.md` as the maintained source of truth", agents)
        self.assertNotIn("CLAUDE.md` is useful, but some parts are stale", agents)
        self.assertNotIn("docs/plans/agent-kinds-refactor.md", agents)

        for heading in (
            "## Project overview",
            "## Key commands",
            "## Never deploy/stop mid-session",
            "## Architecture map",
            "## Persistence and state",
            "### Kinds refactor invariants",
            "### Torque context namespace",
            "## Worker dispatch and reporting",
            "## Code conventions",
            "### Surface-invalidation discipline",
            "## Testing",
            "## Reference docs",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, agents)

        for reference in (
            "docs/reference/architecture.md",
            "docs/reference/hooks-gotchas.md",
            "docs/reference/install-locations.md",
            "docs/reference/specializations.md",
            "docs/operate/manual-testing.md",
        ):
            with self.subTest(reference=reference):
                self.assertIn(reference, agents)
                self.assertTrue((ROOT / reference).exists(), reference)

    def test_completed_root_plan_docs_removed(self):
        self.assertEqual([], sorted(path.name for path in ROOT.glob("*_PLAN.md")))


if __name__ == "__main__":
    unittest.main()
