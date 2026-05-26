import unittest
from datetime import datetime, timezone

from torque.remote_ingress import (
    RemoteIngressError,
    ingest_remote_command_request,
    ingest_remote_user_agent_message,
    normalize_remote_command_request,
    normalize_remote_user_agent_message,
)


class RemoteIngressTests(unittest.IsolatedAsyncioTestCase):
    command_id = "123e4567-e89b-42d3-a456-426614174000"
    now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)

    def test_normalize_rejects_arbitrary_commands(self):
        with self.assertRaises(RemoteIngressError):
            normalize_remote_user_agent_message({
                "cmd": "remove_agent",
                "agent_id": "worker-1",
                "message": "hi",
            })

    def test_normalize_requires_direct_message_shape(self):
        with self.assertRaises(RemoteIngressError):
            normalize_remote_user_agent_message({"message": "hi"})
        with self.assertRaises(RemoteIngressError):
            normalize_remote_user_agent_message({"agent_id": "worker-1"})
        with self.assertRaises(RemoteIngressError):
            normalize_remote_user_agent_message({"agent_id": 7, "message": "hi"})

    async def test_ingest_delegates_to_local_user_agent_message_handler(self):
        calls = []

        async def handler(command, state, send_prompt):
            calls.append((command, state, send_prompt))
            return {"type": "ok", "message_id": "msg-1"}

        state = object()
        send_prompt = object()
        result = await ingest_remote_user_agent_message(
            {
                "target_agent_id": "worker-1",
                "message": "hello from remote",
                "thread_id": "user-agent:user:worker-1",
                "reply_to_id": "msg-parent",
                "client_message_id": "relay-msg-1",
            },
            state=state,
            send_prompt=send_prompt,
            handler=handler,
        )

        self.assertEqual(result, {"type": "ok", "message_id": "msg-1"})
        self.assertEqual(len(calls), 1)
        command, called_state, called_send_prompt = calls[0]
        self.assertIs(called_state, state)
        self.assertIs(called_send_prompt, send_prompt)
        self.assertEqual(command, {
            "cmd": "user_agent_message",
            "agent_id": "worker-1",
            "message": "hello from remote",
            "thread_id": "user-agent:user:worker-1",
            "reply_to_id": "msg-parent",
            "idempotency_key": "relay-msg-1",
        })

    def test_normalize_remote_command_maps_only_restart_agent(self):
        command = normalize_remote_command_request(
            {
                "command_id": self.command_id,
                "cmd": "restart_agent",
                "args": {"agent_id": "worker-1"},
                "confirm": True,
                "issued_at": "2026-05-26T12:00:00Z",
                "nonce": "nonce-1",
            },
            now=self.now,
        )
        self.assertEqual(command, {
            "command_id": self.command_id,
            "cmd": "restart_agent",
            "id": "worker-1",
        })

        with self.assertRaises(RemoteIngressError):
            normalize_remote_user_agent_message({
                "command_id": self.command_id,
                "cmd": "restart_agent",
                "args": {"agent_id": "worker-1"},
            })

    async def test_remote_command_confirm_gate_and_dispatch(self):
        calls = []

        class State:
            agents = {"worker-1": object()}

        async def handler(command):
            calls.append(command)
            return None

        base = {
            "command_id": self.command_id,
            "cmd": "restart_agent",
            "args": {"agent_id": "worker-1"},
            "issued_at": "2026-05-26T12:00:00Z",
            "nonce": "nonce-1",
        }
        missing_confirm = await ingest_remote_command_request(
            dict(base),
            state=State(),
            handler=handler,
            now=self.now,
        )
        self.assertEqual(missing_confirm["status"], "confirmation_required")
        self.assertEqual(calls, [])

        confirmed = await ingest_remote_command_request(
            {**base, "confirm": True},
            state=State(),
            handler=handler,
            now=self.now,
        )
        self.assertEqual(confirmed["status"], "ok")
        self.assertEqual(calls, [{"cmd": "restart_agent", "id": "worker-1"}])

    async def test_remote_command_rejects_unknown_stale_and_missing_agent(self):
        class State:
            agents = {}

        async def handler(_command):
            raise AssertionError("handler must not run")

        result = await ingest_remote_command_request(
            {
                "command_id": self.command_id,
                "cmd": "remove_agent",
                "args": {"agent_id": "worker-1"},
                "confirm": True,
                "issued_at": "2026-05-26T12:00:00Z",
                "nonce": "nonce-1",
            },
            state=State(),
            handler=handler,
            now=self.now,
        )
        self.assertEqual(result["error_code"], "unsupported_command")

        result = await ingest_remote_command_request(
            {
                "command_id": self.command_id,
                "cmd": "restart_agent",
                "args": {"agent_id": "worker-1"},
                "confirm": True,
                "issued_at": "2026-05-26T11:00:00Z",
                "nonce": "nonce-1",
            },
            state=State(),
            handler=handler,
            now=self.now,
        )
        self.assertEqual(result["error_code"], "stale_command")

        result = await ingest_remote_command_request(
            {
                "command_id": self.command_id,
                "cmd": "restart_agent",
                "args": {"agent_id": "worker-1"},
                "confirm": True,
                "issued_at": "2026-05-26T12:00:00Z",
                "nonce": "nonce-1",
            },
            state=State(),
            handler=handler,
            now=self.now,
        )
        self.assertEqual(result["error_code"], "agent_not_found")


if __name__ == "__main__":
    unittest.main()
