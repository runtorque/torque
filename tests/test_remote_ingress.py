import unittest

from torque.remote_ingress import (
    RemoteIngressError,
    ingest_remote_user_agent_message,
    normalize_remote_user_agent_message,
)


class RemoteIngressTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
