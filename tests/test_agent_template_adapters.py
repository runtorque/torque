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

    def test_codex_system_prompt_and_model_flags_use_cli_args(self):
        adapter = CodexAdapter()
        self.assertEqual(
            adapter.inject_system_prompt("/tmp", "Be precise."),
            " --instructions 'Be precise.'",
        )
        self.assertEqual(adapter.resolve_model_flags("gpt-5"), " --model gpt-5")

    def test_generic_adapter_is_noop_for_template_specific_flags(self):
        adapter = GenericAdapter()
        self.assertEqual(adapter.inject_system_prompt("/tmp", "ignored"), "")
        self.assertEqual(adapter.resolve_model_flags("ignored"), "")
