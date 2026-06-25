import tempfile
import unittest
from pathlib import Path

from torque.help_docs import (
    build_help_index,
    handle_help_command,
    list_help_topics,
    query_help,
    search_help,
    show_help_topic,
)


class HelpDocsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "docs" / "reference").mkdir(parents=True)
        (self.root / "docs" / "operate").mkdir(parents=True)
        (self.root / ".torque").mkdir()
        (self.root / "mkdocs.yml").write_text(
            """
site_name: Test Torque
nav:
  - Home: index.md
  - Reference:
      - MCP tools: reference/mcp-tools.md
  - Operate:
      - Manual testing: operate/manual-testing.md
""".strip(),
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            "# Torque\n\nRoot overview for users.\n",
            encoding="utf-8",
        )
        (self.root / "AGENTS.md").write_text(
            "# AGENTS.md\n\nMirror CLAUDE.md for agents.\n",
            encoding="utf-8",
        )
        (self.root / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\nMaintained source of truth.\n",
            encoding="utf-8",
        )
        (self.root / "docs" / "index.md").write_text(
            "# Home\n\nWelcome to Help docs.\n",
            encoding="utf-8",
        )
        (self.root / "docs" / "reference" / "mcp-tools.md").write_text(
            """# MCP tools reference

Read the scoping rules before calling tools.

## Worker tools

Workers use `torque_done` and `torque_derive` to report progress and hand off review.

```bash
torque ai derive "Review" -t feature/review
```
""".strip(),
            encoding="utf-8",
        )
        (self.root / "docs" / "operate" / "manual-testing.md").write_text(
            "# Manual testing\n\nRun smoke checks from a non-worker shell.\n",
            encoding="utf-8",
        )
        (self.root / ".torque" / "secret.md").write_text(
            "# Secret\n\ndo not index\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_help_index_uses_allowlisted_sources_only(self):
        index = build_help_index(self.root)
        paths = set(index.source_paths)
        self.assertIn("README.md", paths)
        self.assertIn("AGENTS.md", paths)
        self.assertIn("CLAUDE.md", paths)
        self.assertIn("docs/reference/mcp-tools.md", paths)
        self.assertIn("docs/operate/manual-testing.md", paths)
        self.assertNotIn(".torque/secret.md", paths)
        self.assertTrue(index.index_hash)

    def test_list_show_search_and_query_return_source_contract_fields(self):
        listed = list_help_topics(base_dir=self.root)
        self.assertEqual(listed["type"], "help_topics")
        topic = next(item for item in listed["topics"] if item["source_path"] == "docs/reference/mcp-tools.md")
        self.assertEqual(topic["topic_id"], "docs-reference-mcp-tools")
        self.assertTrue(topic["restricted_safe"])
        self.assertTrue(topic["source_hash"])
        self.assertTrue(topic["sections"])

        shown = show_help_topic("docs/reference/mcp-tools.md#worker-tools", base_dir=self.root)
        self.assertEqual(shown["status"], "ok")
        self.assertEqual(shown["anchor"], "worker-tools")
        self.assertTrue(shown["index_hash"])
        self.assertIn("torque_derive", shown["body_excerpt"])
        self.assertTrue(shown["examples"])

        searched = search_help("handoff review torque_derive", base_dir=self.root)
        self.assertEqual(searched["status"], "ok")
        self.assertEqual(searched["results"][0]["source_path"], "docs/reference/mcp-tools.md")
        self.assertIn("path_anchor", searched["results"][0])

        answered = query_help("How does a worker hand off review?", base_dir=self.root)
        self.assertEqual(answered["status"], "answered")
        self.assertIn("Source:", answered["answer"])
        self.assertTrue(answered["sources"])

    def test_no_answer_and_path_traversal_are_safe(self):
        no_answer = query_help("zzzz nonexistent phrase", base_dir=self.root)
        self.assertEqual(no_answer["status"], "no_answer")
        self.assertEqual(no_answer["sources"], [])

        traversal = show_help_topic("../.torque/secret.md", base_dir=self.root)
        self.assertEqual(traversal["status"], "not_found")
        self.assertTrue(traversal["index_hash"])

    def test_handle_help_command_shapes(self):
        response = handle_help_command(
            {"cmd": "help_search", "query": "manual testing", "limit": 2},
            base_dir=self.root,
        )
        self.assertEqual(response["type"], "help_search")
        self.assertLessEqual(len(response["results"]), 2)


if __name__ == "__main__":
    unittest.main()
