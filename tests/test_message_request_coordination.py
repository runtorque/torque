"""Keyed HTTP retries share actual terminal delivery and turn cancellation."""
import asyncio
import inspect
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
    install_aiohttp_stub()

from torque import server, server_routes
from torque.commands.agent_operations import AgentOperationRuntime, handle_agent_operation_command
from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState
from torque.server_agent import AgentLaunchService
from torque.local_pty import LocalPtyAdapter
from tests.test_board_creation_requests import Response, json_response


class MessageRequestCoordinationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = TorqueDB(Path(temporary.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.cell = AgentCell(id='message-target', name='Worker', group='g',
                              cell_type='agent', kind='worker',
                              session_id='session-1', status='idle')
        self.state.agents[self.cell.id] = self.cell
        self.state.groups['g'] = [self.cell.id]
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.effects = []

        async def send_text(session_id, text):
            self.effects.append(('send', session_id, text))
            self.entered.set()
            await self.release.wait()

        async def interrupt(session_id):
            self.effects.append(('interrupt', session_id))
            self.entered.set()
            await self.release.wait()
            return True

        self.bridge = SimpleNamespace(send_text=send_text, interrupt_active_turn=interrupt)
        self.sender = AgentLaunchService(state=self.state, connection=None, bridge=self.bridge,
                                         worktree_mgr=None, template_mgr=None)
        values = dict(state=self.state, bridge=self.bridge, send_agent_prompt=self.sender,
                      handle_send_user_message_command=server._handle_send_user_message_command,
                      handle_user_agent_turn_cancel_command=server._handle_user_agent_turn_cancel_command)
        runtime = AgentOperationRuntime(**{field.name: values.get(field.name) for field in fields(AgentOperationRuntime)})

        async def handle(data):
            return await handle_agent_operation_command(data, runtime)

        values = {name: None for name in inspect.signature(server_routes.build_http_routes).parameters}
        values.update(handle_command=handle, db=self.db, state=self.state,
                      daemon_stop_state=SimpleNamespace(should_reject_api_request=lambda _: False),
                      api_worker_context_guard=lambda *_: None)
        self.routes = server_routes.build_http_routes(**values)
        web_patch = patch.object(server_routes, 'web', SimpleNamespace(json_response=json_response, Response=Response))
        web_patch.start()
        self.addCleanup(web_patch.stop)

    def payload(self, command):
        data = {'cmd': command, 'cell_id': self.cell.id, 'agent_id': self.cell.id,
                'session_id': self.cell.session_id, 'text': 'Only deliver once',
                'idempotency_key': command + '-one'}
        if command == 'user_agent_turn_cancel':
            source_key = 'source-turn'
            self.state.save_direct_message({
                'id': server._user_direct_message_id_from_idempotency_key(source_key),
                'thread_id': 'user-agent:user:' + self.cell.id,
                'idempotency_key': source_key, 'group_name': 'g',
                'sender_id': 'user', 'sender_kind': 'user', 'sender_name': 'User',
                'recipient_id': self.cell.id, 'recipient_kind': 'worker',
                'recipient_name': self.cell.name, 'message': 'Interrupt this turn',
                'message_type': 'message', 'delivery_state': 'delivered',
            })
            data['turn_idempotency_key'] = source_key
            message_id = server._user_direct_message_id_from_idempotency_key(source_key)
            self.cell.status = 'running'
            self.sender._user_direct_turns[message_id] = {
                'cell_id': self.cell.id, 'session_id': self.cell.session_id,
                'delivery_started': True,
            }
            self.sender._active_user_direct_turn_by_session[self.cell.session_id] = message_id
        return data

    async def request(self, data):
        return await self.routes.handle_api_cmd(SimpleNamespace(
            json=AsyncMock(return_value=data), headers={}, remote='127.0.0.1'))

    async def overlapping(self, command, *, disconnect=False):
        data = self.payload(command)
        first = asyncio.create_task(self.request(data))
        second = None
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            second = asyncio.create_task(self.request(dict(data)))
            for _ in range(5):
                await asyncio.sleep(0)
            if disconnect:
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
            self.assertEqual(len(self.effects), 1, 'Retry must not repeat the actual delivery effect')
        finally:
            self.release.set()
            responses = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        response = responses[-1]
        self.assertNotIsInstance(response, BaseException)
        self.assertTrue(json.loads(response.body)['ok'])
        if not disconnect:
            self.assertEqual(responses[0].body, response.body)
            self.assertIsNot(responses[0], response)
        cached = await self.request(data)
        self.assertEqual(cached.body, response.body)
        self.assertEqual(len(self.effects), 1)

    async def test_terminal_retry_joins_actual_awaited_delivery(self):
        await self.overlapping('send_user_message')

    async def test_turn_cancel_retry_joins_actual_awaited_interrupt(self):
        await self.overlapping('user_agent_turn_cancel')

    async def test_terminal_delivery_survives_disconnected_first_waiter(self):
        await self.overlapping('send_user_message', disconnect=True)

    async def test_turn_cancel_survives_disconnected_first_waiter(self):
        await self.overlapping('user_agent_turn_cancel', disconnect=True)

    async def test_conflicting_pending_payload_is_refused_without_a_second_effect(self):
        for command in ('send_user_message', 'user_agent_turn_cancel'):
            with self.subTest(command=command):
                self.entered.clear()
                self.release.clear()
                self.effects.clear()
                data = self.payload(command)
                first = asyncio.create_task(self.request(data))
                conflict = None
                try:
                    await asyncio.wait_for(self.entered.wait(), 2)
                    conflict = asyncio.create_task(self.request({**data, 'session_id': 'replacement'}))
                    for _ in range(5):
                        await asyncio.sleep(0)
                    self.assertTrue(conflict.done(), 'Conflict must refuse while the effect is pending')
                    self.assertEqual(conflict.result().status, 409)
                    self.assertEqual(len(self.effects), 1)
                finally:
                    self.release.set()
                    await asyncio.gather(first, *([conflict] if conflict else []))
                self.assertEqual((await self.request({**data, 'session_id': 'replacement'})).status, 409)
                self.assertEqual(len(self.effects), 1)

    async def test_only_waiter_disconnect_does_not_cancel_delivery_or_lose_receipt(self):
        data = self.payload('send_user_message')
        first = asyncio.create_task(self.request(data))
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
        finally:
            self.release.set()
        # Give the detached operation time to persist its acknowledgement.
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertIsNotNone(self.db.load_mcp_idempotency(data['idempotency_key']))
        self.assertTrue(json.loads((await self.request(data)).body)['ok'])
        self.assertEqual(len(self.effects), 1)

    async def test_independent_message_keys_are_not_coalesced(self):
        data = self.payload('send_user_message')
        first = asyncio.create_task(self.request(data))
        second = None
        try:
            await asyncio.wait_for(self.entered.wait(), 2)
            second = asyncio.create_task(self.request({**data, 'idempotency_key': 'another-message', 'text': 'Second message'}))
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertEqual(len(self.effects), 2)
            self.assertEqual({effect[2] for effect in self.effects}, {'Only deliver once', 'Second message'})
        finally:
            self.release.set()
            await asyncio.gather(first, *([second] if second else []))

    async def test_session_replacement_is_refused_before_terminal_effect(self):
        data = self.payload('send_user_message')
        self.cell.session_id = 'replacement'
        response = await self.request(data)
        self.assertFalse(json.loads(response.body)['ok'])
        self.assertEqual(self.effects, [])

    async def test_receipt_save_failure_retries_acknowledgement_without_repeating_effect(self):
        for command in ('send_user_message', 'user_agent_turn_cancel'):
            with self.subTest(command=command):
                self.effects.clear()
                self.release.set()
                data = self.payload(command)
                save = self.db.save_mcp_idempotency
                with patch.object(self.db, 'save_mcp_idempotency', side_effect=RuntimeError('Receipt unavailable')):
                    for _ in range(2):
                        with self.assertRaisesRegex(RuntimeError, 'Receipt unavailable'):
                            await self.request(data)
                    conflict = await self.request({**data, 'text': 'Changed message'})
                    self.assertEqual(conflict.status, 409)
                    for other in ('get_state', 'board_add_task'):
                        conflict = await self.request({'cmd': other, 'idempotency_key': data['idempotency_key']})
                        self.assertEqual(conflict.status, 409)
                self.assertEqual(len(self.effects), 1)
                with patch.object(self.db, 'save_mcp_idempotency', wraps=save) as persist:
                    response = await self.request(data)
                    self.assertTrue(json.loads(response.body)['ok'])
                    persist.assert_called_once()
                self.assertEqual((await self.request(data)).body, response.body)
                self.assertEqual(len(self.effects), 1)

    async def test_absent_or_closed_pty_cannot_acknowledge_delivery_or_record_history(self):
        adapter = LocalPtyAdapter(self.state)
        self.bridge.send_text = adapter.send_text
        for closed in (False, True):
            with self.subTest(closed=closed):
                if closed:
                    adapter._sessions[self.cell.session_id] = SimpleNamespace(closed=True)
                data = {**self.payload('send_user_message'), 'idempotency_key': f'missing-{closed}'}
                response = await self.request(data)
                self.assertFalse(json.loads(response.body)['ok'])
                self.assertEqual(self.cell.status, 'idle')
                self.assertEqual(self.state.agent_message_history_read(self.cell.id), [])
                self.assertIsNone(self.db.load_mcp_idempotency(data['idempotency_key']))
