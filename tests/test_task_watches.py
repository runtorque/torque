import asyncio
import tempfile
import time
import unittest
from pathlib import Path
try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
install_aiohttp_stub()
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState
from torque.server import _handle_user_agent_message_command

class TaskWatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=TorqueDB(Path(self.tmp.name)/'torque.db'); self.db.init(); self.addCleanup(self.db.close)
        self.state=MatrixState(self.db); self.state.groups['g']=[]
        self.agent=AgentCell(id='agent', name='Agent', group='g', cell_type='agent', kind='worker', session_id='s')
        self.other=AgentCell(id='other', name='Other', group='other', cell_type='agent', kind='worker')
        self.state.agents[self.agent.id]=self.agent; self.state.agents[self.other.id]=self.other
        self.state.groups['g']=['agent']; self.state.groups['other']=['other']
        self.one=self.state.board_add_task('One', 'g', lane='To Do', id='G:1')
        self.two=self.state.board_add_task('Two', 'g', lane='To Do', id='G:2')
        self.hidden=self.state.board_add_task('Hidden', 'other', lane='To Do', id='O:1')
    async def _command(self, text, key='k'):
        sent=[]
        async def prompt(*args, **kwargs): sent.append(args)
        result=await _handle_user_agent_message_command({'agent_id':'agent','message':text,'idempotency_key':key}, self.state, prompt)
        self.assertEqual(sent, [])
        return result
    async def test_create_incremental_fire_cancel_and_no_provider_prompt(self):
        created=await self._command('/watch G:1 G:2', 'create')
        self.assertEqual(created['type'],'ok'); watch=self.db.list_task_watches(requester_agent_id='agent',status='active')[0]
        self.state.board_move_task('G:1','Done'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'active')
        listed=await self._command('/watches','list'); self.assertIn('1/2 Done',self.db.load_direct_message(listed['message_id'])['message'])
        cancelled=await self._command('/unwatch '+watch['id'],'cancel'); self.assertIn('Cancelled',self.db.load_direct_message(cancelled['message_id'])['message'])
        self.state.board_move_task('G:2','Done'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')
    async def test_immediate_once_scope_and_bad_forms_are_local(self):
        self.state.board_move_task('G:1','Done')
        result=await self._command('/watch G:1','immediate')
        self.assertEqual(self.db.list_task_watches(requester_agent_id='agent')[0]['status'],'fired')
        self.assertIsNotNone(self.db.load_direct_message(
            self.db.list_task_watches(requester_agent_id='agent')[0]['id'] + ':complete'))
        again=await self._command('/watch G:1','immediate')
        self.assertTrue(again['deduped']); self.assertEqual(len(self.db.list_operator_notices()), 0)
        for i,text in enumerate(('/watch','/watch G:1 G:1','/watch O:1','/WATCH G:1','/unwatch','/watches x')):
            response=await self._command(text,'bad'+str(i)); self.assertEqual(response['type'],'ok')
        sent=[]
        async def prompt(*args, **kwargs): sent.append(args)
        normal=await _handle_user_agent_message_command({'agent_id':'agent','message':'/watchdog','idempotency_key':'normal'}, self.state, prompt)
        self.assertTrue(sent)
        self.assertEqual(self.db.load_direct_message(normal['message_id'])['sender_kind'],'user')
    async def test_expiry_delete_and_restart_reconcile(self):
        watch=self.state.create_task_watch(target=self.agent,task_ids=['G:1'],now=1)
        self.state.reconcile_task_watches(now=31*24*60*60+2); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')
        watch=self.state.create_task_watch(target=self.agent,task_ids=['G:2'])
        self.state.board_remove_task('G:2'); self.assertEqual(self.db.load_task_watch(watch['id'])['status'],'cancelled')

    async def test_watch_bounds_are_enforced_at_command_and_service_boundaries(self):
        for number in range(3, 22):
            self.state.board_add_task(f'Task {number}', 'g', lane='To Do', id=f'G:{number}')
        refs = [f'G:{number}' for number in range(1, 21)]
        created_at = 1234.5
        watch = self.state.create_task_watch(
            target=self.agent, task_ids=refs, now=created_at,
        )
        self.assertEqual(watch['expires_at'], created_at + 30 * 24 * 60 * 60)
        with self.assertRaisesRegex(ValueError, '1–20 unique task IDs'):
            self.state.create_task_watch(target=self.agent, task_ids=refs + ['G:21'])
        response = await self._command('/watch ' + ' '.join(refs + ['G:21']), 'too-many-refs')
        self.assertEqual(response['type'], 'ok')
        self.assertIn('1–20 unique task IDs', self.db.load_direct_message(response['message_id'])['message'])
        for _ in range(99):
            self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.assertEqual(len(self.db.list_task_watches(requester_agent_id='agent', status='active', limit=101)), 100)
        with self.assertRaisesRegex(ValueError, 'At most 100 active watches'):
            self.state.create_task_watch(target=self.agent, task_ids=['G:1'])

    async def test_outbox_failure_reconciles_once_without_rolling_back_fired(self):
        self.state.board_move_task('G:1', 'Done')
        original = self.state.save_direct_message
        def fail_once(row):
            if row.get('id', '').endswith(':complete'):
                raise RuntimeError('thread row unavailable')
            return original(row)
        self.state.save_direct_message = fail_once
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.assertEqual(watch['status'], 'fired')
        self.assertEqual(self.db.load_task_watch(watch['id'])['outbox_state'], 'pending')
        self.assertIsNone(self.db.load_direct_message(watch['id'] + ':complete'))
        self.state.save_direct_message = original
        self.state.reconcile_task_watches()
        self.assertEqual(self.db.load_task_watch(watch['id'])['outbox_state'], 'sent')
        self.assertIsNotNone(self.db.load_direct_message(watch['id'] + ':complete'))
        self.state.reconcile_task_watches()
        self.assertEqual(len([row for row in self.db.load_direct_messages_for_thread(watch['thread_id'])
                              if row['id'] == watch['id'] + ':complete']), 1)

    async def test_unwatch_all_and_requester_isolation(self):
        one = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.other.group = 'g'
        two = self.state.create_task_watch(target=self.other, task_ids=['G:1'])
        response = await self._command('/unwatch all', 'all')
        self.assertIn('Cancelled 1 active', self.db.load_direct_message(response['message_id'])['message'])
        self.assertEqual(self.db.load_task_watch(one['id'])['status'], 'cancelled')
        self.assertEqual(self.db.load_task_watch(two['id'])['status'], 'active')

    async def test_watch_command_audits_reject_idempotency_reuse_for_different_text(self):
        first = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        second = self.state.create_task_watch(target=self.agent, task_ids=['G:2'])
        response = await self._command('/unwatch ' + first['id'], 'same-command-key')
        self.assertEqual(response['type'], 'ok')
        conflict = await self._command('/unwatch ' + second['id'], 'same-command-key')
        self.assertEqual(conflict['type'], 'error')
        self.assertEqual(self.db.load_task_watch(second['id'])['status'], 'active')
        await self._command('/watches', 'list-command-key')
        conflict = await self._command('/unwatch all', 'list-command-key')
        self.assertEqual(conflict['type'], 'error')
        self.assertEqual(self.db.load_task_watch(second['id'])['status'], 'active')

    async def test_thread_row_is_durable_before_optional_fanout_and_fanout_failure_is_terminal(self):
        self.state.board_move_task('G:1', 'Done')
        class BrokenNotifier:
            def __init__(self, state): self.state = state; self.rows = []
            def on_task_watch(self, watch):
                self.rows.append(self.state.db.load_direct_message(watch['id'] + ':complete'))
                raise RuntimeError('desktop unavailable')
        notifier = BrokenNotifier(self.state)
        self.state.notification_manager = notifier
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.assertEqual(watch['status'], 'fired')
        self.assertEqual(self.db.load_task_watch(watch['id'])['outbox_state'], 'sent')
        self.assertIsNotNone(notifier.rows[0], 'fanout runs only after durable thread commit')
        self.assertEqual(len(self.db.list_operator_notices()), 0)
        self.state.reconcile_task_watches()
        self.assertEqual(self.db.load_task_watch(watch['id'])['outbox_state'], 'sent')
        self.assertEqual(len([row for row in self.db.load_direct_messages_for_thread(watch['thread_id'])
                              if row['id'] == watch['id'] + ':complete']), 1)

    async def test_watch_retry_after_audit_failure_reuses_durable_request_watch(self):
        self.state.board_move_task('G:1', 'Done')
        original = self.state.save_direct_message

        def fail_watch_audit(row):
            if (row.get('context_snapshot') or {}).get('command_response') == 'watch':
                raise RuntimeError('audit write failed')
            return original(row)

        self.state.save_direct_message = fail_watch_audit
        with self.assertRaises(RuntimeError):
            await self._command('/watch G:1', 'request-retry')
        watch = self.db.list_task_watches(requester_agent_id='agent')[0]
        self.assertEqual(len(self.db.list_task_watches(requester_agent_id='agent')), 1)
        self.assertIsNotNone(self.db.load_direct_message(watch['id'] + ':complete'))
        self.state.save_direct_message = original
        retried = await self._command('/watch G:1', 'request-retry')
        self.assertEqual(retried['type'], 'ok')
        self.assertEqual(len(self.db.list_task_watches(requester_agent_id='agent')), 1)
        self.assertEqual(len([row for row in self.db.load_direct_messages_for_thread(watch['thread_id'])
                              if row['id'] == watch['id'] + ':complete']), 1)
        again = await self._command('/watch G:1', 'request-retry')
        self.assertTrue(again['deduped'])

    async def test_watch_retry_after_audit_crash_is_recovered_without_duplicates(self):
        self.state.board_move_task('G:1', 'Done')
        original = self.state.save_direct_message

        def crash_watch_audit(row):
            if (row.get('context_snapshot') or {}).get('command_response') == 'watch':
                raise SystemExit('simulated audit-process crash')
            return original(row)

        self.state.save_direct_message = crash_watch_audit
        with self.assertRaises(SystemExit):
            await self._command('/watch G:1', 'request-crash')
        watch = self.db.list_task_watches(requester_agent_id='agent')[0]
        self.state.save_direct_message = original
        retried = await self._command('/watch G:1', 'request-crash')
        self.assertEqual(retried['type'], 'ok')
        self.assertEqual(len(self.db.list_task_watches(requester_agent_id='agent')), 1)
        self.assertEqual(len([row for row in self.db.load_direct_messages_for_thread(watch['thread_id'])
                              if row['id'] == watch['id'] + ':complete']), 1)
        conflict = await self._command('/watch G:2', 'request-crash')
        self.assertEqual(conflict['type'], 'error')
        self.assertEqual(len(self.db.list_task_watches(requester_agent_id='agent')), 1)

    def _save_paged_watch(self, watch_id, *, expires_at, status='active'):
        now = time.time()
        return self.db.save_task_watch({
            'id': watch_id, 'requester_agent_id': 'agent',
            'thread_id': 'user:agent', 'group_name': 'g', 'task_ids': ['G:1'],
            'created_at': now, 'expires_at': expires_at, 'status': status,
            'fired_at': 0, 'cancelled_at': 0, 'dedupe_key': 'task-watch:' + watch_id,
            'request_idempotency_key': '', 'outbox_state': 'pending',
            'outbox_attempted_at': 0, 'updated_at': now,
        })

    async def test_global_watch_scans_paginate_past_one_thousand_without_duplicates(self):
        for index in range(1001):
            self._save_paged_watch(f'page-fire-{index:04}', expires_at=time.time() + 3600)
        self.state.board_move_task('G:1', 'Done')
        self.assertEqual(self.db.load_task_watch('page-fire-1000')['status'], 'fired')
        self.assertIsNotNone(self.db.load_direct_message('page-fire-1000:complete'))
        self.assertEqual(self.db._conn.execute("SELECT COUNT(*) FROM agent_peer_messages WHERE id LIKE 'page-fire-%:complete'").fetchone()[0], 1001)
        self.state.reconcile_task_watches()
        self.assertEqual(self.db._conn.execute("SELECT COUNT(*) FROM agent_peer_messages WHERE id LIKE 'page-fire-%:complete'").fetchone()[0], 1001)
        for index in range(1001):
            self._save_paged_watch(f'page-expire-{index:04}', expires_at=1)
        self.state.reconcile_task_watches(now=2)
        self.assertEqual(self.db.load_task_watch('page-expire-1000')['status'], 'cancelled')
        self.assertEqual(self.db._conn.execute("SELECT COUNT(*) FROM agent_peer_messages WHERE id LIKE 'page-fire-%:complete'").fetchone()[0], 1001)

    async def test_minimal_db_keeps_event_driven_evaluation_a_safe_noop(self):
        class MinimalDB:
            pass
        self.state.db = MinimalDB()
        self.state.evaluate_task_watches_for_task('G:1')
        self.state.reconcile_task_watches()
        self.assertEqual(self.state.list_task_watches(self.agent), [])
        self.assertEqual(self.state.cancel_task_watch(self.agent, 'all'), 0)

    async def test_soft_delete_cancels_active_watch_before_task_delivery(self):
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.state.remove_agent(self.agent.id)
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')
        self.state.board_move_task('G:1', 'Done')
        self.assertEqual(self.db.list_operator_notices(), [])

    async def test_hard_delete_move_and_rename_invalidate_requester_scope(self):
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.state.move_agent(self.agent.id, 'other')
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')
        self.agent.group = 'g'
        self.state.groups['other'].remove(self.agent.id)
        self.state.groups['g'].append(self.agent.id)
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.state.rename_group('g', 'renamed')
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')
        self.agent.group = 'renamed'
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.state.purge_agent_now(self.agent.id)
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')

    async def test_lifecycle_invalidation_stops_fired_pending_outbox_delivery(self):
        self.state.board_move_task('G:1', 'Done')
        original = self.state.save_direct_message
        self.state.save_direct_message = lambda row: (_ for _ in ()).throw(RuntimeError('fail'))
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'fired')
        self.state.remove_agent(self.agent.id)
        self.state.save_direct_message = original
        self.state.reconcile_task_watches()
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')
        self.assertEqual(self.db.list_operator_notices(), [])

    async def test_unwatch_only_reports_an_atomic_active_cancel_claim(self):
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        original = self.db.claim_task_watch_cancelled
        def fire_before_cancel(watch_id, *, cancelled_at):
            self.db.claim_task_watch_fired(watch_id, fired_at=cancelled_at)
            return original(watch_id, cancelled_at=cancelled_at)
        self.db.claim_task_watch_cancelled = fire_before_cancel
        response = await self._command('/unwatch ' + watch['id'], 'race')
        self.assertIn('No matching active', self.db.load_direct_message(response['message_id'])['message'])
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'fired')

    async def test_reconcile_retries_crashed_final_delivery_gate(self):
        """A crash after the final gate remains a durable, retryable outbox."""
        self.state.board_move_task('G:1', 'Done')
        original_gate = self.db.claim_task_watch_notice_delivery

        def crash_after_final_gate(watch_id, *, attempted_at):
            if original_gate(watch_id, attempted_at=attempted_at):
                raise SystemExit('simulated process stop')
            return False

        self.db.claim_task_watch_notice_delivery = crash_after_final_gate
        with self.assertRaises(SystemExit):
            self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        watch = self.db.list_task_watches(requester_agent_id='agent', status='fired')[0]
        self.assertEqual(watch['outbox_state'], 'notifying')
        self.db.claim_task_watch_notice_delivery = original_gate
        self.state.reconcile_task_watches()
        self.assertEqual(self.db.load_task_watch(watch['id'])['outbox_state'], 'sent')
        self.assertIsNotNone(self.db.load_direct_message(watch['id'] + ':complete'))

    async def test_lifecycle_invalidation_winning_after_outbox_claim_never_notifies(self):
        """The final DB gate orders requester teardown before Inbox delivery."""
        self.state.board_move_task('G:1', 'Done')
        original_gate = self.db.claim_task_watch_notice_delivery

        def invalidate_before_notice_gate(watch_id, *, attempted_at):
            # This runs after the pending->sending claim, at the formerly unsafe
            # interleaving boundary, but before any notice/thread side effect.
            self.state.remove_agent(self.agent.id)
            return original_gate(watch_id, attempted_at=attempted_at)

        self.db.claim_task_watch_notice_delivery = invalidate_before_notice_gate
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        stored = self.db.load_task_watch(watch['id'])
        self.assertEqual(stored['status'], 'cancelled')
        self.assertEqual(stored['outbox_state'], 'cancelled')
        self.assertEqual(self.db.list_operator_notices(), [])
        self.assertIsNone(self.db.load_direct_message(watch['id'] + ':complete'))

    async def test_lifecycle_invalidation_after_final_claim_never_writes_thread_row(self):
        """The notifying state remains cancellable until the thread row exists."""
        self.state.board_move_task('G:1', 'Done')
        original_load = self.db.load_task_watch
        invalidated = False

        def invalidate_after_final_claim(watch_id):
            nonlocal invalidated
            row = original_load(watch_id)
            if (not invalidated and row and row.get('outbox_state') == 'notifying'):
                invalidated = True
                self.state.remove_agent(self.agent.id)
            return original_load(watch_id)

        self.db.load_task_watch = invalidate_after_final_claim
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        stored = original_load(watch['id'])
        self.assertTrue(invalidated)
        self.assertEqual(stored['status'], 'cancelled')
        self.assertEqual(stored['outbox_state'], 'cancelled')
        self.assertIsNone(self.db.load_direct_message(watch['id'] + ':complete'))

    async def test_task_scope_loss_after_final_claim_cancels_only_that_watch(self):
        self.state.board_move_task('G:1', 'Done')
        unaffected = self.state.create_task_watch(target=self.agent, task_ids=['G:2'])
        original_gate = self.db.claim_task_watch_notice_delivery

        def hide_task_after_final_claim(watch_id, *, attempted_at):
            claimed = original_gate(watch_id, attempted_at=attempted_at)
            if claimed:
                self.one.group = 'hidden-scope'
            return claimed

        self.db.claim_task_watch_notice_delivery = hide_task_after_final_claim
        watch = self.state.create_task_watch(target=self.agent, task_ids=['G:1'])
        self.assertEqual(self.db.load_task_watch(watch['id'])['status'], 'cancelled')
        self.assertEqual(self.db.load_task_watch(unaffected['id'])['status'], 'active')
        self.assertIsNone(self.db.load_direct_message(watch['id'] + ':complete'))
