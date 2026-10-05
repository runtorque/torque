"""Buffered composer sends acknowledge actual delivery and preserve session identity."""
import asyncio
import unittest
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock

try:
    import aiohttp  # noqa: F401
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub
    install_aiohttp_stub()

from torque.commands.agent_operations import AgentOperationRuntime, handle_agent_operation_command


class TerminalComposerParityTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, sender):
        state = SimpleNamespace(agents={'shell': SimpleNamespace(session_id='session-1')})
        values = {'state': state, 'bridge': object(), 'handle_send_user_message_command': sender}
        return AgentOperationRuntime(**{field.name: values.get(field.name) for field in fields(AgentOperationRuntime)})

    async def test_acknowledgement_waits_for_delivery(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def deliver(*args):
            entered.set()
            await release.wait()
            return True

        runtime = self.runtime(AsyncMock(side_effect=deliver))
        data = {'cmd': 'send_user_message', 'cell_id': 'shell', 'session_id': 'session-1', 'text': 'hello'}
        task = asyncio.create_task(handle_agent_operation_command(data, runtime))
        await entered.wait()
        self.assertFalse(task.done())
        release.set()
        self.assertEqual(await task, {'type': 'terminal_message_sent', 'cell_id': 'shell', 'session_id': 'session-1'})
        runtime.handle_send_user_message_command.assert_awaited_once_with(data, runtime.state, runtime.bridge)

    async def test_replaced_session_is_refused_before_delivery(self):
        sender = AsyncMock(return_value=True)
        frame = await handle_agent_operation_command({'cmd': 'send_user_message', 'cell_id': 'shell', 'session_id': 'old', 'text': 'hello'}, self.runtime(sender))
        self.assertEqual(frame['type'], 'error')
        self.assertIn('session changed', frame['message'])
        sender.assert_not_awaited()

    async def test_legacy_requests_still_deliver_and_false_is_not_success(self):
        sender = AsyncMock(return_value=True)
        runtime = self.runtime(sender)
        data = {'cmd': 'send_user_message', 'id': 'shell', 'text': 'hello'}
        self.assertEqual((await handle_agent_operation_command(data, runtime))['cell_id'], 'shell')
        sender.return_value = False
        self.assertEqual((await handle_agent_operation_command(data, runtime))['type'], 'error')

    async def test_exception_cannot_be_acknowledged_as_delivery(self):
        runtime = self.runtime(AsyncMock(side_effect=RuntimeError('delivery failed')))
        with self.assertRaisesRegex(RuntimeError, 'delivery failed'):
            await handle_agent_operation_command({'cmd': 'send_user_message', 'cell_id': 'shell', 'text': 'hello'}, runtime)
