import importlib
import types
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

    def test_board_archive_command_ignores_include_descendants_and_archives_one_task(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        parent = state.board_add_task('Done parent', 'g', lane='Done', id='task-1')
        child = state.board_add_task(
            'Done child',
            'g',
            lane='Done',
            id='task-2',
            parent_task_id='task-1',
        )

        result = self.server_mod._handle_board_archive_command(
            state,
            {'id': parent.id, 'include_descendants': True},
        )

        self.assertIsNone(result)
        self.assertEqual(state.board_tasks[parent.id].lane, 'Archived')
        self.assertEqual(state.board_tasks[child.id].lane, 'Done')

    def test_board_unarchive_command_ignores_include_descendants_and_restores_one_task(self):
        state = self.state_mod.MatrixState()
        state.add_group('g')
        parent = state.board_add_task('Done parent', 'g', lane='Done', id='task-1')
        child = state.board_add_task(
            'Done child',
            'g',
            lane='Done',
            id='task-2',
            parent_task_id='task-1',
        )
        state.board_archive_task(parent.id)
        state.board_archive_task(child.id)

        result = self.server_mod._handle_board_unarchive_command(
            state,
            {'id': parent.id, 'lane': 'Done', 'include_descendants': True},
        )

        self.assertIsNone(result)
        self.assertEqual(state.board_tasks[parent.id].lane, 'Done')
        self.assertEqual(state.board_tasks[child.id].lane, 'Archived')


class ServerMergeCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module('loom.state')
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module('loom.server')
        self.server_mod = importlib.reload(self.server_mod)

    async def test_relaunch_after_worktree_removal_resets_live_agent_session(self):
        cell = self.state_mod.AgentCell(
            id='agent-1',
            name='Worker',
            group='g',
            cell_type='agent',
            session_id='session-old',
            agent_session_id='resume-token',
            status='running',
            directory='/repo',
            template='default',
        )
        emitted = []
        saved = []
        launch_calls = []
        prompt_calls = []
        closed = []
        created = []

        async def fake_resolve_base_dir(_group):
            return '/fallback'

        def fake_resolve_agent_launch_config(group, *, base_dir,
                                             explicit_template, overrides):
            launch_calls.append({
                'group': group,
                'base_dir': base_dir,
                'explicit_template': explicit_template,
                'overrides': overrides,
            })
            return {
                'env_vars': {'LOOM_ENV': '1'},
                'env_file': '/tmp/loom.env',
                'shell': 'zsh',
                'system_prompt': 'system prompt',
            }

        def fake_apply_persistent_prompt(cell, launch_cfg, prompt):
            prompt_calls.append({
                'directory': cell.directory,
                'prompt': prompt,
                'env_file': launch_cfg.get('env_file'),
            })

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            return f"{launch_cfg.get('system_prompt')} @ {cell.directory}"

        class FakeBridge:
            async def close_session(self, session_id):
                closed.append(session_id)

            async def create_session(self, cell, **kwargs):
                cell.session_id = 'session-new'
                created.append({
                    'directory': cell.directory,
                    'kwargs': kwargs,
                })

        fake_state = types.SimpleNamespace(
            _emit_agent=lambda cell: emitted.append(
                (cell.status, cell.directory, cell.session_id)
            ),
            _db_save_agent=lambda cell: saved.append(
                (cell.status, cell.directory, cell.session_id)
            ),
        )

        await self.server_mod._relaunch_agent_after_worktree_removal(
            cell,
            bridge=FakeBridge(),
            state=fake_state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=fake_resolve_agent_launch_config,
            apply_persistent_prompt=fake_apply_persistent_prompt,
            build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
        )

        self.assertEqual(closed, ['session-old'])
        self.assertEqual(cell.session_id, 'session-new')
        self.assertEqual(cell.agent_session_id, '')
        self.assertEqual(cell.status, 'stopped')
        self.assertEqual(
            launch_calls,
            [{
                'group': 'g',
                'base_dir': '/repo',
                'explicit_template': 'default',
                'overrides': {},
            }],
        )
        self.assertEqual(
            prompt_calls,
            [{
                'directory': '/repo',
                'prompt': 'system prompt @ /repo',
                'env_file': '/tmp/loom.env',
            }],
        )
        self.assertEqual(
            created,
            [{
                'directory': '/repo',
                'kwargs': {
                    'env_vars': {'LOOM_ENV': '1'},
                    'env_file': '/tmp/loom.env',
                    'shell': 'zsh',
                    'system_prompt': 'system prompt',
                },
            }],
        )
        self.assertEqual(
            emitted,
            [('stopped', '/repo', None)],
        )
        self.assertEqual(
            saved,
            [('stopped', '/repo', None)],
        )


if __name__ == '__main__':
    unittest.main()
