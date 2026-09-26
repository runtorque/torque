"""HTTP creation retries must share an in-flight write before its receipt exists."""
import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from torque.db import TorqueDB
from torque import server_routes


class Response:
    def __init__(self, *, body=b'', status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}


def json_response(payload, status=200):
    return Response(body=json.dumps(payload).encode(), status=status)


class BoardCreationRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = TorqueDB(Path(temporary.name) / 'torque.db')
        self.db.init()
        self.addCleanup(self.db.close)
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.calls = []

        async def create(data):
            self.calls.append(dict(data))
            self.entered.set()
            await self.release.wait()  # e.g. awaited action/deliverable resolution
            return {'type': 'board_task_added', 'task_id': f'QA:{len(self.calls)}', 'title': data['task']}

        values = {name: None for name in inspect.signature(server_routes.build_http_routes).parameters}
        values.update(handle_command=create, db=self.db,
                      daemon_stop_state=SimpleNamespace(should_reject_api_request=lambda _: False),
                      api_worker_context_guard=lambda *_: None)
        self.routes = server_routes.build_http_routes(**values)
        self.patch = patch.object(server_routes, 'web', SimpleNamespace(json_response=json_response, Response=Response))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.payload = {'cmd': 'board_add_task', 'id': 'draft-test', 'task': 'Reviewed draft', 'group': 'QA', 'idempotency_key': 'create-one'}

    async def request(self, **changes):
        return await self.routes.handle_api_cmd(SimpleNamespace(json=AsyncMock(return_value={**self.payload, **changes}), headers={}, remote='127.0.0.1'))

    async def test_overlapping_retry_joins_creation_even_when_first_waiter_disconnects(self):
        first = asyncio.create_task(self.request())
        await self.entered.wait()
        second = asyncio.create_task(self.request())
        await asyncio.sleep(0)
        first.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            await second
        response = json.loads(second.result().body)
        self.assertEqual(response['data']['task_id'], 'QA:1')
        cached = await self.request()
        self.assertEqual(json.loads(cached.body), response)
        self.assertEqual(len(self.calls), 1)

    async def test_schedule_creation_retry_joins_pending_creation(self):
        first = asyncio.create_task(self.request(cmd='schedule_create'))
        await self.entered.wait()
        second = asyncio.create_task(self.request(cmd='schedule_create'))
        await asyncio.sleep(0)
        try:
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            responses = await asyncio.gather(first, second)
        self.assertEqual(responses[0].body, responses[1].body)
        self.assertEqual(len(self.calls), 1)

    async def test_changed_payload_cannot_join_active_creation_key(self):
        first = asyncio.create_task(self.request())
        await self.entered.wait()
        conflict = asyncio.create_task(self.request(task='Changed draft'))
        for _ in range(5):
            await asyncio.sleep(0)
        try:
            self.assertTrue(conflict.done(), 'Conflicting retry should refuse without waiting for or executing creation')
            self.assertEqual(conflict.result().status, 409)
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
            await asyncio.gather(first, conflict)

    async def test_completed_receipt_replays_and_rejects_changed_payload(self):
        self.release.set()
        first = await self.request()
        second = await self.request()
        self.assertEqual(json.loads(first.body), json.loads(second.body))
        self.assertIsNot(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.request(task='Changed draft')).status, 409)


class BoardCreationRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_refusal_is_explicit_but_partial_mutation_exception_is_not(self):
        from torque.commands.board_operations import BoardOperationRuntime, handle_board_operation_command
        state = SimpleNamespace(agents={}, get_group_settings=lambda _: SimpleNamespace(board_default_lane='Backlog', board_default_action='', board_default_labels=[]), board_add_task=Mock(return_value=None))
        values = {field.name: None for field in fields(BoardOperationRuntime)}
        values.update(state=state, normalize_external_link=lambda *_: {'provider': '', 'external_id': '', 'external_url': ''})
        runtime = BoardOperationRuntime(**values)
        result = await handle_board_operation_command({'cmd': 'board_add_task', 'task': 'Draft', 'group': 'QA'}, runtime)
        self.assertTrue(result['creation_refused'])
        state.board_add_task.side_effect = RuntimeError('Persistence failed after mutation')
        with self.assertRaisesRegex(RuntimeError, 'Persistence failed'):
            await handle_board_operation_command({'cmd': 'board_add_task', 'task': 'Draft', 'group': 'QA'}, runtime)

    async def test_http_exposes_only_explicit_creation_refusal(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                result = {'type': 'error', 'message': 'Could not create'}
                if explicit:
                    result['creation_refused'] = True
                values = {name: None for name in inspect.signature(server_routes.build_http_routes).parameters}
                values.update(handle_command=AsyncMock(return_value=result), daemon_stop_state=SimpleNamespace(should_reject_api_request=lambda _: False), api_worker_context_guard=lambda *_: None)
                routes = server_routes.build_http_routes(**values)
                with patch.object(server_routes, 'web', SimpleNamespace(json_response=json_response, Response=Response)):
                    response = await routes.handle_api_cmd(SimpleNamespace(json=AsyncMock(return_value={'cmd': 'board_add_task', 'task': 'Draft'}), headers={}, remote='127.0.0.1'))
                self.assertEqual(json.loads(response.body).get('creation_refused', False), explicit)
