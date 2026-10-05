"""Keyed launch failures must retain the exact target, not create another one."""
import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from torque import server_routes
from torque.db import TorqueDB
from torque.state import MatrixState


class Response:
    def __init__(self, *, body=b'', status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}


def json_response(payload, status=200):
    return Response(body=json.dumps(payload).encode(), status=status)


class CreationOutcomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = TorqueDB(Path(temporary.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.add_group('QA')
        self.calls = []
        self.handler = None

        async def handle(data):
            self.calls.append(dict(data))
            return await self.handler(data)

        values = {name: None for name in inspect.signature(server_routes.build_http_routes).parameters}
        values.update(state=self.state, handle_command=handle, db=self.db,
                      daemon_stop_state=SimpleNamespace(should_reject_api_request=lambda _: False),
                      api_worker_context_guard=lambda *_: None)
        self.routes = server_routes.build_http_routes(**values)
        patcher = patch.object(server_routes, 'web', SimpleNamespace(json_response=json_response, Response=Response))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def request(self, **changes):
        data = {'cmd': 'add_terminal', 'name': 'Reviewed launch', 'group': 'QA', 'idempotency_key': 'launch-one', **changes}
        response = await self.routes.handle_api_cmd(SimpleNamespace(json=AsyncMock(return_value=data), headers={}, remote='127.0.0.1'))
        return response, json.loads(response.body)

    async def test_terminal_exception_after_creation_returns_inspectable_cached_target(self):
        async def launch(data):
            self.state.add_terminal(name=data['name'], group=data['group'])
            raise RuntimeError('PTY startup failed after allocation')
        self.handler = launch
        _, first = await self.request()
        self.assertTrue(first['ok'])
        frame = first['data']
        self.assertEqual(frame['type'], 'creation_incomplete')
        self.assertEqual(frame['command'], 'add_terminal')
        self.assertEqual(frame['idempotency_key'], 'launch-one')
        self.assertIn('PTY startup failed', frame['message'])
        target = frame['target']
        self.assertEqual(target['type'], 'agent')
        self.assertEqual(target['id'], next(iter(self.state.agents)))
        self.assertEqual(target['kind'], 'terminal')
        self.assertEqual(target['name'], 'Reviewed launch')
        _, again = await self.request()
        self.assertEqual(again, first)
        self.assertEqual(len(self.calls), 1)
        conflict, _ = await self.request(name='Changed launch')
        self.assertEqual(conflict.status, 409)

    async def test_returned_failure_after_agent_creation_is_not_a_safe_refusal(self):
        async def launch(data):
            cell = self.state.add_agent(name=data['name'], group=data['group'])
            cell.kind = 'worker'
            return {'type': 'error', 'message': 'Startup prompt failed'}
        self.handler = launch
        _, response = await self.request(cmd='add_worker')
        self.assertTrue(response['ok'])
        self.assertEqual(response['data']['type'], 'creation_incomplete')
        self.assertEqual(response['data']['target']['kind'], 'worker')
        self.assertNotIn('creation_refused', response)

    async def test_preallocation_refusal_does_not_freeze_or_cache_the_draft(self):
        async def refused(_):
            return {'type': 'error', 'message': 'Name is required'}
        self.handler = refused
        _, response = await self.request()
        self.assertEqual(response, {'ok': False, 'error': 'Name is required', 'creation_refused': True})
        self.assertIsNone(self.db.load_mcp_idempotency('launch-one'))
        await self.request(name='Corrected')
        self.assertEqual(len(self.calls), 2)

    async def test_verified_target_rollback_permits_editing_again(self):
        async def launch(data):
            cell = self.state.add_agent(name=data['name'], group=data['group'])
            self.state._hard_delete_agent(cell.id)
            raise RuntimeError('Launch rolled back')
        self.handler = launch
        _, response = await self.request(cmd='add_worker')
        self.assertFalse(response['ok'])
        self.assertTrue(response['creation_refused'])
        self.assertEqual(self.state.agents, {})

    async def test_saved_hire_with_failed_delta_is_recovered_by_its_exact_id(self):
        async def launch(data):
            saved = await self.state.save_pending_hire_async({'id': 'hire-reviewed', 'architect_id': 'arch', 'requested_name': data['name'], 'status': 'pending'})
            self.assertIsNone(saved)
            return {'type': 'error', 'message': 'Failed to create pending hire'}
        self.handler = launch
        with patch.object(self.state, '_emit_pending_hire', side_effect=RuntimeError('Delta failed')):
            _, response = await self.request(cmd='architect_engineer_hire', architect_id='arch')
        self.assertTrue(response['ok'])
        self.assertEqual(response['data']['target'], {'type': 'pending_hire', 'id': 'hire-reviewed', 'name': 'Reviewed launch', 'architect_id': 'arch', 'status': 'pending'})
        await self.request(cmd='architect_engineer_hire', architect_id='arch')
        self.assertEqual(len(self.calls), 1)

    async def test_receipt_save_failure_does_not_reexecute_a_successful_launch(self):
        async def launch(data):
            cell = self.state.add_terminal(name=data['name'], group=data['group'])
            return {'type': 'terminal_created', 'id': cell.id, 'name': cell.name, 'kind': 'terminal', 'parent_id': ''}
        self.handler = launch
        with patch.object(self.db, 'save_mcp_idempotency', side_effect=RuntimeError('Receipt temporarily unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Receipt temporarily unavailable'):
                await self.request()
        _, response = await self.request()
        self.assertTrue(response['ok'])
        self.assertEqual(response['data']['id'], next(iter(self.state.agents)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.state.agents), 1)

    async def test_parallel_launches_keep_their_own_target_identity(self):
        ready = asyncio.Event()
        release = asyncio.Event()
        ids = {}
        async def launch(data):
            cell = self.state.add_terminal(name=data['name'], group=data['group'])
            ids[data['name']] = cell.id
            if data['name'] == 'First':
                ready.set()
                await release.wait()
            else:
                release.set()
            raise RuntimeError('Failed after target allocation')
        self.handler = launch
        first = asyncio.create_task(self.request(name='First', idempotency_key='first'))
        await ready.wait()
        _, second = await self.request(name='Second', idempotency_key='second')
        _, first = await first
        self.assertEqual(first['data']['target']['id'], ids['First'])
        self.assertEqual(second['data']['target']['id'], ids['Second'])

    async def test_durable_agent_survives_failed_memory_rollback(self):
        async def launch(data):
            cell = self.state.add_terminal(name=data['name'], group=data['group'])
            self.state.agents.pop(cell.id)
            raise RuntimeError('Failed to delete persisted target')
        self.handler = launch
        _, response = await self.request()
        self.assertTrue(response['ok'])
        target = response['data']['target']
        self.assertEqual(target['kind'], 'terminal')
        self.assertIn(target['id'], self.db.load_all()['agents'])

    async def test_verification_failure_never_reexecutes_or_releases_the_draft(self):
        async def launch(data):
            cell = self.state.add_terminal(name=data['name'], group=data['group'])
            self.state.agents.pop(cell.id)
            raise RuntimeError('Incomplete rollback')
        self.handler = launch
        with patch('torque.services.creation_outcomes.load_created_agent', side_effect=RuntimeError('Lookup unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Lookup unavailable'):
                await self.request()
        conflict, _ = await self.request(name='Changed draft')
        self.assertEqual(conflict.status, 409)
        _, response = await self.request()
        self.assertEqual(response['data']['type'], 'creation_incomplete')
        self.assertEqual(len(self.calls), 1)

    async def test_pending_hire_lookup_failure_remains_unknown_until_verified(self):
        async def launch(data):
            await self.state.save_pending_hire_async({'id': 'hire-uncertain', 'architect_id': 'arch', 'requested_name': data['name']})
            return {'type': 'error', 'message': 'Delivery failed'}
        self.handler = launch
        # Async persistence owns a separate connection; this facade lookup is
        # the subsequent outcome verification only.
        with patch.object(self.db, 'load_pending_hire', side_effect=RuntimeError('Hire lookup unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'Hire lookup unavailable'):
                await self.request(cmd='architect_engineer_hire')
        _, response = await self.request(cmd='architect_engineer_hire')
        self.assertEqual(response['data']['target']['id'], 'hire-uncertain')
        self.assertEqual(len(self.calls), 1)

    async def test_mutation_failure_before_add_returns_still_retains_target(self):
        async def launch(data):
            self.state.add_terminal(name=data['name'], group=data['group'])
        self.handler = launch
        with patch.object(self.state, '_emit_agent', side_effect=RuntimeError('Emit failed')):
            _, response = await self.request()
        self.assertEqual(response['data']['target']['id'], next(iter(self.state.agents)))
        self.assertEqual(response['data']['type'], 'creation_incomplete')

    async def test_late_child_context_cannot_claim_an_unrelated_allocation(self):
        from torque.services.creation_outcomes import CreationOutcome
        release = asyncio.Event()
        tasks = []
        async def launch(_):
            async def child():
                await release.wait()
                self.state.add_terminal(name='Background target', group='QA')
            tasks.append(asyncio.create_task(child()))
            return {'type': 'error', 'message': 'Refused before allocation'}
        outcome = CreationOutcome('add_terminal', 'key', 'hash', 'Requested')
        await outcome.execute(launch, {})
        release.set()
        await tasks[0]
        self.assertEqual(outcome.target_id, '')
        self.assertTrue(outcome.resolve(self.state, self.db)['creation_refused'])

    async def test_changed_command_cannot_reuse_an_unsettled_creation_key(self):
        async def launch(data):
            cell = self.state.add_terminal(name=data['name'], group=data['group'])
            return {'type': 'terminal_created', 'id': cell.id}
        self.handler = launch
        with patch.object(self.db, 'save_mcp_idempotency', side_effect=RuntimeError('Receipt unavailable')):
            with self.assertRaises(RuntimeError):
                await self.request()
        response, _ = await self.request(cmd='update_agent', id='unrelated')
        self.assertEqual(response.status, 409)
        self.assertEqual(len(self.calls), 1)
