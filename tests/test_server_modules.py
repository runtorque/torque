import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerModuleExtractionTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.server_actions = importlib.import_module('loom.server_actions')
        self.server_actions = importlib.reload(self.server_actions)
        self.server_dispatch = importlib.import_module('loom.server_dispatch')
        self.server_dispatch = importlib.reload(self.server_dispatch)
        self.server_worktrees = importlib.import_module('loom.server_worktrees')
        self.server_worktrees = importlib.reload(self.server_worktrees)
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)

    def test_parse_unified_diff_summarizes_added_file(self):
        diff_text = (
            'diff --git a/new.txt b/new.txt\n'
            'new file mode 100644\n'
            'index 0000000..1111111\n'
            '--- /dev/null\n'
            '+++ b/new.txt\n'
            '@@ -0,0 +1,2 @@\n'
            '+hello\n'
            '+world\n'
        )

        files = self.server_worktrees._parse_unified_diff(diff_text)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], 'new.txt')
        self.assertEqual(files[0]['status'], 'added')
        self.assertEqual(files[0]['insertions'], 2)
        self.assertEqual(files[0]['deletions'], 0)

    def test_action_to_yaml_keeps_prompt_as_block_scalar(self):
        yaml_text = self.server_actions._action_to_yaml('review/code', {
            'description': 'Review code',
            'prompt': 'Line 1\nLine 2',
            'labels': ['review'],
        })

        self.assertIn('name: review/code', yaml_text)
        self.assertIn('prompt: |', yaml_text)
        self.assertIn('  Line 1', yaml_text)
        self.assertIn('labels:', yaml_text)

    def test_dispatch_queue_helper_respects_self_dispatch(self):
        active = self.state_mod.BoardTask(
            id='task-1',
            task='Parent task',
            group='g',
            lane='In Progress',
        )

        self.assertTrue(self.server_dispatch._should_queue_existing_agent_dispatch(
            active,
            target_task_id='task-2',
            self_dispatch=False,
        ))
        self.assertFalse(self.server_dispatch._should_queue_existing_agent_dispatch(
            active,
            target_task_id='task-2',
            self_dispatch=True,
        ))

    def test_standalone_mode_skips_keybinding_installation(self):
        self.assertFalse(self.server_mod.STANDALONE)
        self.assertTrue(self.server_mod._should_install_keybindings())

        old = self.server_mod.STANDALONE
        try:
            self.server_mod.STANDALONE = True
            self.assertFalse(self.server_mod._should_install_keybindings())
        finally:
            self.server_mod.STANDALONE = old


if __name__ == '__main__':
    unittest.main()
