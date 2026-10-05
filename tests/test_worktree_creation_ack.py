import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from torque.commands.worktrees import WorktreeCommandRuntime, handle_worktree_command


class WorktreeCreationAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cell = SimpleNamespace(id='worker', name='Worker', group='qa', directory='/repo',
                                    deleted_at=0, worktree_path='', worktree_branch='',
                                    worktree_repo_root='', worktree_base_dir='', worktree_base_branch='',
                                    session_id=None, agent_session_id='', template='', agent_type='', status='stopped')
        settings = SimpleNamespace(worktree_base_branch='main', worktree_symlinks=[])
        self.state = SimpleNamespace(agents={'worker': self.cell}, get_group_settings=lambda _: settings,
                                     _emit_agent=Mock(), _db_save_agent=Mock())
        async def create(cell, root, **_kwargs):
            cell.worktree_path = root + '/.torque/worktrees/worker'
            cell.worktree_repo_root = root
            cell.worktree_branch = 'torque/user/worker'
            return cell.worktree_path
        async def close(_session): self.cell.session_id = None
        async def launch(cell, **_kwargs): cell.session_id = 'new-session'
        self.manager = SimpleNamespace(get_repo_root=AsyncMock(return_value='/repo'), create=AsyncMock(side_effect=create))
        self.bridge = SimpleNamespace(close_session=AsyncMock(side_effect=close), create_session=AsyncMock(side_effect=launch))
        values = {field.name: None for field in fields(WorktreeCommandRuntime)}
        values.update(state=self.state, worktree_mgr=self.manager, bridge=self.bridge,
                      launch_resolver_for_cell=lambda *_args, **_kwargs: lambda *_a, **_k: {'shell': '/bin/sh'},
                      apply_persistent_prompt=Mock(), build_cell_persistent_prompt=lambda *_: '',
                      runtime_env_vars_for_cell=lambda *_: {}, mcp_entrypoint_for_cell=lambda _: '')
        self.runtime = WorktreeCommandRuntime(**values)

    async def command(self, **extra):
        return await handle_worktree_command({'cmd': 'worktree_create', 'id': 'worker', **extra}, self.runtime)

    async def test_stopped_creation_acknowledges_path_without_starting_session(self):
        result = await self.command(expected_session_id='')
        self.assertTrue(result['ok']); self.assertTrue(result['created'])
        self.assertEqual(result['type'], 'worktree_create'); self.assertEqual(result['id'], 'worker')
        self.assertEqual(result['worktree_path'], self.cell.worktree_path)
        self.assertFalse(result['relaunched']); self.assertEqual(result['session_id'], '')
        self.bridge.create_session.assert_not_awaited()
        self.state._db_save_agent.assert_called_once_with(self.cell)

    async def test_missing_deleted_and_non_repository_targets_refuse_creation(self):
        self.state.agents = {}
        self.assertFalse((await self.command())['ok'])
        self.state.agents = {'worker': self.cell}; self.cell.deleted_at = 1
        self.assertFalse((await self.command())['ok'])
        self.cell.deleted_at = 0; self.manager.get_repo_root.return_value = None
        self.assertIn('Git repository', (await self.command())['error'])
        self.manager.create.assert_not_awaited(); self.bridge.close_session.assert_not_awaited()

    async def test_creation_failure_is_not_a_success_snapshot(self):
        self.manager.create.side_effect = None; self.manager.create.return_value = None
        result = await self.command()
        self.assertFalse(result['ok']); self.assertFalse(result['created'])
        self.assertEqual(result['phase'], 'create'); self.state._emit_agent.assert_not_called()
        self.manager.create.side_effect = RuntimeError('Branch collision')
        self.assertIn('Branch collision', (await self.command())['error'])

    async def test_reviewed_session_must_match_before_any_creation_or_close(self):
        self.cell.session_id = 'newer-session'
        result = await self.command(expected_session_id='reviewed-session', relaunch=True)
        self.assertFalse(result['ok']); self.assertEqual(result['phase'], 'session_changed')
        self.manager.create.assert_not_awaited(); self.bridge.close_session.assert_not_awaited()

    async def test_relaunch_confirms_a_distinct_new_session(self):
        self.cell.session_id = 'old-session'
        result = await self.command(expected_session_id='old-session', relaunch=True)
        self.assertTrue(result['ok']); self.assertTrue(result['relaunched'])
        self.assertEqual(result['session_id'], 'new-session')
        self.bridge.close_session.assert_awaited_once_with('old-session')
        self.assertEqual(self.cell.directory, result['worktree_path'])

    async def test_failed_relaunch_can_resume_without_recreating_the_worktree(self):
        self.cell.session_id = 'old-session'; self.bridge.create_session.side_effect = RuntimeError('PTY unavailable')
        failed = await self.command(expected_session_id='old-session', relaunch=True)
        self.assertFalse(failed['ok']); self.assertTrue(failed['created']); self.assertTrue(failed['resume_available'])
        self.assertEqual(failed['session_id'], ''); self.assertIn('PTY unavailable', failed['error'])
        async def launch(cell, **_kwargs): cell.session_id = 'recovered-session'
        self.bridge.create_session.side_effect = launch
        result = await self.command(expected_session_id='', relaunch=True, resume_worktree_path=failed['worktree_path'])
        self.assertTrue(result['ok']); self.assertTrue(result['relaunched']); self.assertEqual(result['session_id'], 'recovered-session')
        self.manager.create.assert_awaited_once(); self.bridge.close_session.assert_awaited_once()

    async def test_existing_or_replaced_worktree_cannot_be_recreated_or_resumed(self):
        await self.command()
        self.assertFalse((await self.command())['ok'])
        self.assertFalse((await self.command(relaunch=True, resume_worktree_path='/different'))['ok'])
        self.manager.create.assert_awaited_once(); self.bridge.close_session.assert_not_awaited()

    async def test_target_change_during_repository_read_prevents_creation(self):
        async def root(_path): self.cell.session_id = 'other'; return '/repo'
        self.manager.get_repo_root.side_effect = root
        result = await self.command(expected_session_id='', relaunch=True)
        self.assertFalse(result['ok']); self.manager.create.assert_not_awaited()

    async def test_session_change_during_creation_does_not_restart_the_new_session(self):
        original = self.manager.create.side_effect
        async def create(*args, **kwargs):
            path = await original(*args, **kwargs); self.cell.session_id = 'other'; return path
        self.manager.create.side_effect = create
        result = await self.command(expected_session_id='', relaunch=True)
        self.assertFalse(result['ok']); self.assertTrue(result['created']); self.assertTrue(result['resume_available'])
        self.assertEqual(result['session_id'], 'other'); self.bridge.close_session.assert_not_awaited()

    async def test_deleted_during_creation_is_not_resurrected_by_persistence(self):
        original = self.manager.create.side_effect
        async def create(*args, **kwargs):
            path = await original(*args, **kwargs); self.state.agents.clear(); return path
        self.manager.create.side_effect = create
        result = await self.command()
        self.assertFalse(result['ok']); self.assertTrue(result['created']); self.assertFalse(result['resume_available'])
        self.state._db_save_agent.assert_not_called(); self.bridge.create_session.assert_not_awaited()

    async def test_session_change_while_closing_prevents_new_launch(self):
        self.cell.session_id = 'reviewed'
        async def close(_session): self.cell.session_id = 'replacement'
        self.bridge.close_session.side_effect = close
        result = await self.command(expected_session_id='reviewed', relaunch=True)
        self.assertFalse(result['ok']); self.assertEqual(result['session_id'], 'replacement')
        self.bridge.create_session.assert_not_awaited()

    async def test_relaunch_without_new_session_is_reported_as_partial_failure(self):
        self.bridge.create_session.side_effect = None
        result = await self.command(relaunch=True, expected_session_id='')
        self.assertFalse(result['ok']); self.assertTrue(result['created']); self.assertTrue(result['resume_available'])
        self.assertIn('not confirmed', result['error'])
