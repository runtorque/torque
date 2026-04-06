import tempfile
import unittest
from pathlib import Path

from loom.adapters.claude_code import ClaudeCodeAdapter
from loom.adapters.codex import CodexAdapter
from loom.adapters.generic import GenericAdapter


class AgentTemplateAdapterTests(unittest.TestCase):
    def test_claude_inject_system_prompt_writes_instructions_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            result = adapter.inject_system_prompt(tmp, "Follow the spec.")
            self.assertEqual(result, "")
            self.assertEqual(
                (Path(tmp) / ".claude" / "instructions.md").read_text(),
                "Follow the spec.\n",
            )
            self.assertEqual(adapter.resolve_model_flags("sonnet"), " --model sonnet")

    def test_claude_persistent_prompts_use_named_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudeCodeAdapter()
            result = adapter.inject_persistent_prompt(
                tmp, "loom-system-prompt-agent-1.md", "First prompt."
            )
            self.assertTrue(
                result.startswith(" --append-system-prompt-file ")
            )
            self.assertTrue(
                result.endswith("/.loom/loom-system-prompt-agent-1.md")
            )
            adapter.inject_persistent_prompt(
                tmp, "loom-system-prompt-agent-2.md", "Second prompt."
            )
            loom_dir = Path(tmp) / ".loom"
            self.assertEqual(
                (loom_dir / "loom-system-prompt-agent-1.md").read_text(),
                "First prompt.\n",
            )
            self.assertEqual(
                (loom_dir / "loom-system-prompt-agent-2.md").read_text(),
                "Second prompt.\n",
            )
            adapter.uninstall_persistent_prompt(tmp, "loom-system-prompt-agent-1.md")
            self.assertFalse((loom_dir / "loom-system-prompt-agent-1.md").exists())
            self.assertTrue((loom_dir / "loom-system-prompt-agent-2.md").exists())

    def test_codex_system_prompt_and_model_flags_use_cli_args(self):
        adapter = CodexAdapter()
        self.assertEqual(
            adapter.inject_system_prompt("/tmp", "Be precise."),
            " --instructions 'Be precise.'",
        )
        self.assertEqual(adapter.resolve_model_flags("gpt-5"), " --model gpt-5")

    def test_codex_persistent_prompts_use_cli_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CodexAdapter()
            result = adapter.inject_persistent_prompt(
                tmp, "loom-system-prompt-agent-1.md", "First prompt."
            )
            self.assertEqual(
                result,
                " --instructions 'First prompt.'",
            )
            self.assertFalse(
                (Path(tmp) / ".codex" / "AGENTS.md").exists()
            )

    def test_codex_resume_command_preserves_flags(self):
        adapter = CodexAdapter()
        resumed = adapter.get_resume_command(
            "codex --model gpt-5 --instructions 'Follow the spec.'",
            "session-123",
        )
        self.assertEqual(
            resumed,
            "codex resume session-123 --model gpt-5 --instructions 'Follow the spec.'",
        )

    def test_generic_adapter_is_noop_for_template_specific_flags(self):
        adapter = GenericAdapter()
        self.assertEqual(adapter.inject_system_prompt("/tmp", "ignored"), "")
        self.assertEqual(adapter.resolve_model_flags("ignored"), "")
