import copy
import os
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from torque.commands.worktrees import WorktreeCommandRuntime, handle_worktree_command


class WorktreeRemovalReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cell = SimpleNamespace(id='owner', name='Owner', group='qa', deleted_at=0,
            worktree_path='/repo/wt', worktree_repo_root='/repo', worktree_branch='topic',
            worktree_base_branch='main', directory='/repo/wt', current_path='', git_root='',
            session_id=None, status='stopped', worktree_dirty=True, worktree_checkpoints=2)
        self.other = SimpleNamespace(id='peer', name='Peer', group='qa', deleted_at=0,
            worktree_path='/repo/wt', directory='/repo/wt', current_path='', git_root='', session_id='peer-session')
        self.state = SimpleNamespace(agents={'owner': self.cell, 'peer': self.other},
            agent_is_tombstoned=lambda cell: bool(cell.deleted_at), _emit_agent=Mock(), _db_save_agent=Mock())
        self.git_review = patch('torque.commands.worktree_removal.removal_git_review', new_callable=AsyncMock).start()
        self.addCleanup(patch.stopall)
        self.git_review.return_value = dict(head='abc', base_head='base', changes_digest='digest', dirty=True, ignored_files=False, checkpoints=2)
        self.reason = ''
        values = {field.name: None for field in fields(WorktreeCommandRuntime)}
        callbacks = dict(state=self.state, target_has_driverless_payload=lambda _: False,
            safe_remove_worktree_result=AsyncMock(return_value={'ok': True, 'worktree_removed': True}),
            worktree_removal_refusal_reason=lambda *_: self.reason,
            worktree_path_contains=lambda root, path: bool(path) and os.path.commonpath([root, path]) == root)
        values.update({key: value for key, value in callbacks.items() if key in values})
        self.runtime = WorktreeCommandRuntime(**values)

    async def preview(self):
        return await handle_worktree_command({'cmd': 'worktree_remove_preview', 'id': 'owner'}, self.runtime)

    async def remove(self, review, **extra):
        return await handle_worktree_command({'cmd': 'worktree_remove', 'id': 'owner', 'removal_review': review, **extra}, self.runtime)

    async def test_preview_describes_link_only_shared_and_dirty_context_without_mutating(self):
        result = await self.preview()
        self.assertIsInstance(result, dict)
        self.assertEqual(result['type'], 'worktree_remove_preview')
        self.assertEqual(result['review']['mode'], 'unlink')
        self.assertEqual(result['review']['shared_ids'], ['peer'])
        self.assertEqual(result['shared_with'], [{'id': 'peer', 'name': 'Peer'}])
        self.assertTrue(result['review']['dirty']); self.assertEqual(result['review']['checkpoints'], 2)
        self.assertEqual(self.cell.worktree_path, '/repo/wt'); self.state._db_save_agent.assert_not_called()

    async def test_unlink_keeps_peer_metadata_files_and_branch_without_calling_git_removal(self):
        before = copy.deepcopy(vars(self.other)); review = (await self.preview())['review']
        result = await self.remove(review)
        self.assertTrue(result['ok']); self.assertTrue(result['link_cleared']); self.assertFalse(result['worktree_removed'])
        self.assertEqual(result['mode'], 'unlink'); self.assertEqual(result['worktree_path'], '/repo/wt')
        self.assertEqual(vars(self.other), before); self.assertEqual(self.cell.directory, '/repo')
        self.assertEqual(self.cell.worktree_path, ''); self.assertEqual(self.cell.worktree_branch, '')
        self.runtime.safe_remove_worktree_result.assert_not_awaited(); self.state._db_save_agent.assert_called_once_with(self.cell)

    async def test_review_rejects_changed_path_session_sharing_dirty_state_or_branch(self):
        for field, value in [('worktree_path', '/repo/new'), ('session_id', 'new'), ('worktree_branch', 'new')]:
            with self.subTest(field=field):
                review = (await self.preview())['review']; previous = getattr(self.cell, field); setattr(self.cell, field, value)
                self.assertFalse((await self.remove(review))['ok']); setattr(self.cell, field, previous)
        review = (await self.preview())['review']; self.state.agents.pop('peer')
        self.assertFalse((await self.remove(review))['ok']); self.assertEqual(self.cell.worktree_path, '/repo/wt')
        self.runtime.safe_remove_worktree_result.assert_not_awaited()

    async def test_active_queued_and_session_guards_cannot_be_bypassed_by_unlink(self):
        for reason in ['active/fresh agent', 'queued follow-up']:
            self.reason = reason; result = await self.preview(); self.assertIn(reason, result['blocked_reason'])
            refused = await self.remove(result['review']); self.assertFalse(refused['ok']); self.assertIn(reason, refused['error'])
        self.reason = ''; self.cell.session_id = 'attached-but-stopped'
        preview = await self.preview(); self.assertTrue(preview['blocked_reason']); self.assertFalse((await self.remove(preview['review']))['ok'])
        self.runtime.safe_remove_worktree_result.assert_not_awaited(); self.state._db_save_agent.assert_not_called()

    async def test_unshared_removal_uses_existing_safe_git_removal_and_keeps_refusals(self):
        self.state.agents.pop('peer'); preview = await self.preview(); self.assertEqual(preview['review']['mode'], 'remove')
        self.runtime.safe_remove_worktree_result.return_value = {'ok': False, 'worktree_removed': False, 'message': 'Unmerged branch retained'}
        result = await self.remove(preview['review']); self.assertFalse(result['ok']); self.assertIn('Unmerged branch retained', result['error'])
        self.assertEqual(self.cell.directory, '/repo/wt')
        self.runtime.safe_remove_worktree_result.return_value = {'ok': True, 'worktree_removed': True}
        result = await self.remove(preview['review']); self.assertTrue(result['ok']); self.assertTrue(result['worktree_removed']); self.assertEqual(self.cell.directory, '/repo')

    async def test_changed_sharing_cannot_turn_a_reviewed_delete_into_shared_deletion(self):
        self.state.agents.pop('peer'); review = (await self.preview())['review']; self.state.agents['peer'] = self.other
        self.assertFalse((await self.remove(review))['ok']); self.runtime.safe_remove_worktree_result.assert_not_awaited()

    async def test_directory_users_count_as_shared_and_tombstones_do_not(self):
        self.other.worktree_path = ''; self.other.directory = '/repo/wt/subdir'
        self.assertEqual((await self.preview())['review']['mode'], 'unlink')
        self.other.deleted_at = 1; self.assertEqual((await self.preview())['review']['mode'], 'remove')

    async def test_missing_deleted_and_malformed_reviews_refuse_without_writes(self):
        self.assertFalse((await self.remove({}))['ok']); self.assertFalse((await self.remove('wrong'))['ok'])
        self.cell.deleted_at = 1; self.assertFalse((await self.preview())['ok']); self.assertFalse((await self.remove({}))['ok'])
        self.state.agents.clear(); self.assertFalse((await self.preview())['ok']); self.assertFalse((await self.remove({}))['ok'])
        self.runtime.safe_remove_worktree_result.assert_not_awaited()

    async def test_unlink_requires_repository_destination_and_cannot_request_unreviewed_relaunch(self):
        review = (await self.preview())['review']; self.assertFalse((await self.remove(review, relaunch=True))['ok'])
        self.cell.worktree_repo_root = ''; review = (await self.preview())['review']; self.assertFalse((await self.remove(review))['ok'])
        self.assertEqual(self.cell.worktree_path, '/repo/wt'); self.state._db_save_agent.assert_not_called()

    async def test_git_evidence_is_fresh_and_read_failure_refuses_without_removal(self):
        self.cell.worktree_dirty = False
        review = (await self.preview())['review']
        self.assertTrue(review['dirty'])
        self.git_review.return_value = {**self.git_review.return_value, 'changes_digest': 'changed'}
        self.assertFalse((await self.remove(review))['ok'])
        self.git_review.return_value = {'error': 'Cannot inspect current worktree changes'}
        self.assertFalse((await self.preview())['ok'])
        self.assertFalse((await self.remove(review))['ok'])
        self.runtime.safe_remove_worktree_result.assert_not_awaited()

    async def test_users_changing_during_git_probe_require_a_new_review(self):
        evidence = self.git_review.return_value
        async def changed(*_):
            self.state.agents.pop('peer', None)
            return evidence
        self.git_review.side_effect = changed
        self.assertFalse((await self.preview())['ok'])
        self.runtime.safe_remove_worktree_result.assert_not_awaited()

    async def test_physical_release_reports_retained_branch_without_retrying_deletion(self):
        self.state.agents.pop('peer')
        self.runtime.safe_remove_worktree_result.return_value = dict(ok=False, worktree_removed=True, branch_deleted=False)
        result = await self.remove((await self.preview())['review'])
        self.assertTrue(result['ok']); self.assertTrue(result['worktree_removed'])
        self.assertFalse(result['branch_deleted']); self.assertIn('Branch retained: topic', result['message'])


class RemovalGitEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from pathlib import Path
        self.directory = tempfile.TemporaryDirectory(prefix='torque-removal-review-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'qa@example.invalid')
        self.git('config', 'user.name', 'Torque QA')
        (self.root / '.gitignore').write_text('ignored.txt\n')
        (self.root / 'tracked.txt').write_text('baseline\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Baseline')
        self.git('checkout', '-b', 'topic')

    def git(self, *args):
        import subprocess
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    async def review(self, **kwargs):
        from torque.worktree_manager.removal_review import removal_git_review
        return await removal_git_review(kwargs.get('path', str(self.root)), kwargs.get('branch', 'topic'), kwargs.get('base', 'main'))

    async def test_real_git_counts_commits_and_reads_uncached_dirty_and_ignored_files(self):
        clean = await self.review()
        self.assertFalse(clean['dirty']); self.assertFalse(clean['ignored_files']); self.assertEqual(clean['checkpoints'], 0)
        (self.root / 'tracked.txt').write_text('checkpoint\n')
        self.git('add', '.'); self.git('commit', '-m', 'Checkpoint')
        (self.root / 'tracked.txt').write_text('unsaved\n')
        (self.root / 'ignored.txt').write_text('local ignored data\n')
        dirty = await self.review()
        self.assertTrue(dirty['dirty']); self.assertTrue(dirty['ignored_files']); self.assertEqual(dirty['checkpoints'], 1)
        self.assertNotEqual(clean['head'], dirty['head']); self.assertNotEqual(clean['changes_digest'], dirty['changes_digest'])
        (self.root / 'tracked.txt').write_text('edited again\n')
        self.assertNotEqual(dirty['changes_digest'], (await self.review())['changes_digest'])
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')
        self.assertTrue((self.root / 'ignored.txt').exists())

    async def test_missing_directory_unresolved_base_wrong_branch_and_subdirectory_fail_closed(self):
        (self.root / 'sub').mkdir()
        for params in [dict(path=str(self.root / 'missing')), dict(path=str(self.root / 'sub')), dict(base='missing'), dict(branch='other')]:
            with self.subTest(params=params):
                self.assertIn('error', await self.review(**params))
