import unittest

from loom.actions import ActionManager


class ActionAgentTemplateCompatTests(unittest.TestCase):
    def test_render_action_supports_string_agent_template(self):
        raw = """
name: research
agent: researcher
prompt: |
  {{ TASK }}
"""
        rendered = ActionManager().render_action(raw, {"TASK": "Investigate bug"})
        self.assertEqual(rendered["agent_template"], "researcher")
        self.assertEqual(rendered["command"], "")
        self.assertEqual(rendered["prompt"].strip(), "Investigate bug")

    def test_render_action_keeps_legacy_inline_agent_block(self):
        raw = """
name: implement
agent:
  command: codex
  directory: /repo
prompt: |
  {{ TASK }}
"""
        rendered = ActionManager().render_action(raw, {"TASK": "Ship fix"})
        self.assertEqual(rendered["agent_template"], "")
        self.assertEqual(rendered["command"], "codex")
        self.assertEqual(rendered["directory"], "/repo")

