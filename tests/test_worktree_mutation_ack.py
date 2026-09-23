import asyncio
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from torque.commands.worktrees import WorktreeCommandRuntime, handle_worktree_command
from torque.worktree_requests import (
    ACKNOWLEDGED_WORKTREE_MUTATIONS, PendingWorktreeWrites, WorktreeRequestConflict,
)


class WorktreeAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cell = SimpleNamespace(id='worker', name='Worker', group='qa',
                                    worktree_path='/isolated', deleted_at=0)
        self.state = SimpleNamespace(agents={'worker': self.cell}, board_tasks={},
                                     _emit_agent=Mock(), _db_save_agent=Mock(),
                                     get_group_settings=lambda _: SimpleNamespace(board_sync_github={}))
        values = {field.name: None for field in fields(WorktreeCommandRuntime)}
        values.update(state=self.state, worktree_mgr=SimpleNamespace(rollback=AsyncMock(return_value=True)),
                      checkpoint_worktree_with_submodules=AsyncMock(return_value='abc123'),
                      checkpoint_message=lambda _: 'Checkpoint',
                      shared_review_checkpoint_block_reason=lambda *_: '')
        self.runtime = WorktreeCommandRuntime(**values)

    async def run_command(self, command, **extra):
        return await handle_worktree_command({'cmd': command, 'id': 'worker', **extra}, self.runtime)

    async def test_checkpoint_ack_distinguishes_commit_and_clean_noop(self):
        for sha in ('abc123', None):
            with self.subTest(sha=sha):
                self.runtime.checkpoint_worktree_with_submodules.return_value = sha
                result = await self.run_command('worktree_checkpoint')
                self.assertEqual(result['type'], 'worktree_checkpoint')
                self.assertEqual(result['id'], 'worker')
                self.assertTrue(result['ok'])
                self.assertEqual(result['created'], bool(sha))
                self.assertEqual(result['sha'], sha or '')
                self.runtime.checkpoint_worktree_with_submodules.assert_awaited_with(
                    self.cell, 'Checkpoint', raise_on_error=True)

    async def test_checkpoint_failure_propagates_without_emitting_success(self):
        self.runtime.checkpoint_worktree_with_submodules.side_effect = RuntimeError('Git rejected commit')
        with self.assertRaisesRegex(RuntimeError, 'Git rejected commit'):
            await self.run_command('worktree_checkpoint')
        self.state._emit_agent.assert_not_called()
        self.state._db_save_agent.assert_not_called()

    async def test_checkpoint_and_rollback_reject_missing_deleted_and_unisolated_targets(self):
        for kind in ('missing', 'deleted', 'no-worktree'):
            with self.subTest(kind=kind):
                self.state.agents = {} if kind == 'missing' else {'worker': self.cell}
                self.cell.deleted_at = 1 if kind == 'deleted' else 0
                self.cell.worktree_path = '' if kind == 'no-worktree' else '/isolated'
                for command in ('worktree_checkpoint', 'worktree_rollback'):
                    result = await self.run_command(command, sha='abc123')
                    self.assertEqual(result['type'], 'error')
                    self.assertEqual(result['id'], 'worker')
        self.runtime.checkpoint_worktree_with_submodules.assert_not_awaited()
        self.runtime.worktree_mgr.rollback.assert_not_awaited()

    async def test_rollback_failure_is_not_an_acknowledgement(self):
        self.runtime.worktree_mgr.rollback.return_value = False
        result = await self.run_command('worktree_rollback', sha='abc123')
        self.assertEqual(result['type'], 'error')
        self.assertIn('Rollback failed', result['message'])
        self.state._emit_agent.assert_not_called()
        self.state._db_save_agent.assert_not_called()

    async def test_rollback_ack_identifies_the_restored_checkpoint(self):
        result = await self.run_command('worktree_rollback', sha='abc123')
        self.assertEqual(result, {'type': 'worktree_rollback', 'id': 'worker', 'ok': True,
                                  'sha': 'abc123', 'message': 'Checkpoint restored'})
        self.state._emit_agent.assert_called_once_with(self.cell)
        self.assertEqual((await self.run_command('worktree_rollback'))['type'], 'error')

    async def test_remove_missing_target_returns_refusal_instead_of_snapshot(self):
        self.state.agents = {}
        self.runtime.target_has_driverless_payload = lambda _: False
        result = await self.run_command('worktree_remove')
        self.assertEqual(result, {'type': 'error', 'id': 'worker',
                                  'message': 'Agent not found'})

    async def test_pr_results_always_identify_target_and_outcome(self):
        self.runtime.resolve_worktree_command_target_value = AsyncMock(return_value=(self.cell, self.cell, None))
        self.runtime.target_has_driverless_payload = lambda _: False
        self.runtime.rewrite_pr_torque_task_refs_metadata = lambda title, body, **_: {'title': title, 'body': body}
        self.runtime.log_pr_task_ref_rewrite = Mock()
        self.runtime.configured_worktree_submodules_for_cell = lambda *_: []
        for response in ({'url': 'https://example.invalid/pr/1'}, {'url': 'https://example.invalid/pr/2', 'pending_ee_pr': True}, {'error': 'PR refused'}):
            with self.subTest(response=response):
                self.runtime.worktree_mgr.create_pr = AsyncMock(return_value=response)
                result = await self.run_command('worktree_create_pr')
                self.assertEqual(result['id'], 'worker')
                self.assertEqual(result['type'], 'worktree_pr')
                self.assertEqual(result['ok'], 'error' not in response)


class PendingWorktreeWriteTests(unittest.IsolatedAsyncioTestCase):
    def test_scope_is_only_the_six_acknowledged_worktree_mutations(self):
        self.assertEqual(ACKNOWLEDGED_WORKTREE_MUTATIONS, {
            'worktree_checkpoint', 'worktree_rollback', 'worktree_rebase',
            'worktree_remove', 'worktree_create_pr', 'worktree_merge',
        })

    async def test_matching_retry_shares_running_write_after_first_waiter_disconnects(self):
        pending = PendingWorktreeWrites(); started = asyncio.Event(); release = asyncio.Event(); calls = []
        async def operation():
            calls.append('write'); started.set(); await release.wait(); return {'ok': True}
        first = asyncio.create_task(pending.run('key', 'hash', operation))
        await started.wait()
        second = asyncio.create_task(pending.run('key', 'hash', operation))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError): await first
        self.assertEqual(calls, ['write'])
        release.set()
        self.assertEqual(await second, {'ok': True})
        self.assertEqual(pending._pending, {})

    async def test_changed_payload_cannot_join_an_active_key(self):
        pending = PendingWorktreeWrites(); started = asyncio.Event(); release = asyncio.Event()
        async def operation(): started.set(); await release.wait(); return True
        first = asyncio.create_task(pending.run('key', 'one', operation))
        await started.wait()
        with self.assertRaises(WorktreeRequestConflict):
            await pending.run('key', 'two', operation)
        release.set(); self.assertTrue(await first)

    async def test_failure_releases_key_and_unrelated_keys_do_not_block_each_other(self):
        pending = PendingWorktreeWrites(); entered = []; release = asyncio.Event()
        async def operation(): entered.append(True); await release.wait(); raise RuntimeError('failure')
        first = asyncio.create_task(pending.run('one', 'hash', operation))
        second = asyncio.create_task(pending.run('two', 'hash', operation))
        while len(entered) < 2: await asyncio.sleep(0)
        release.set()
        for task in (first, second):
            with self.assertRaisesRegex(RuntimeError, 'failure'): await task
        self.assertEqual(pending._pending, {})
