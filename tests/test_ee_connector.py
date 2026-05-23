import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

EE_PYTHON = Path(__file__).resolve().parents[1] / "ee" / "python"
if str(EE_PYTHON) not in sys.path:
    sys.path.insert(0, str(EE_PYTHON))

from torque_ee_connector.connector import (  # noqa: E402
    EnterpriseConnector,
    build_daemon_ws_url,
    config_from_context,
)
from torque_ee_connector.protocol import (  # noqa: E402
    RELAY_MESSAGE_KINDS,
    make_relay_envelope,
)


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send_str(self, text):
        self.sent.append(json.loads(text))


class EnterpriseConnectorTests(unittest.IsolatedAsyncioTestCase):
    def test_protocol_kind_contract_matches_relay_v1(self):
        self.assertEqual(RELAY_MESSAGE_KINDS, (
            "hello",
            "ready",
            "ping",
            "pong",
            "snapshot_request",
            "snapshot",
            "user_message",
            "agent_message",
            "ask",
            "ask_reply",
            "ack",
            "error",
            "channel_event",
        ))

    def test_build_daemon_ws_url_accepts_base_or_full_local_url(self):
        self.assertEqual(
            build_daemon_ws_url("http://127.0.0.1:8787", "daemon-1"),
            "ws://127.0.0.1:8787/v1/daemon/daemon-1/ws",
        )
        self.assertEqual(
            build_daemon_ws_url("127.0.0.1:8787", "daemon-1"),
            "ws://127.0.0.1:8787/v1/daemon/daemon-1/ws",
        )
        self.assertEqual(
            build_daemon_ws_url(
                "ws://localhost:8787/v1/daemon/daemon-1/ws",
                "ignored",
            ),
            "ws://localhost:8787/v1/daemon/daemon-1/ws",
        )

    def test_config_rejects_non_loopback_phase3_relays(self):
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={
                "relay_url": "https://relay.example.com",
                "daemon_id": "daemon-1",
            },
        )
        with self.assertRaisesRegex(ValueError, "only supports local"):
            config_from_context(context)


    def test_signed_remote_config_degrades_cleanly_without_cryptography(self):
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={
                "relay_url": "https://relay.example.com",
                "daemon_id": "daemon-1",
                "credential_id": "cred-1",
                "private_key_pem": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
            },
        )
        with mock.patch("torque_ee_connector.connector.cryptography_available", return_value=False):
            with self.assertRaisesRegex(ValueError, "cryptography is unavailable"):
                config_from_context(context)

    async def test_user_message_routes_to_remote_ingress_and_acks(self):
        calls = []

        async def remote_ingress(payload):
            calls.append(payload)
            return {"type": "ok", "message_id": "msg-local"}

        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"},
            remote_user_agent_message=remote_ingress,
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        fake_ws = FakeWs()
        connector._ws = fake_ws

        envelope = make_relay_envelope(
            id="msg-remote-1",
            daemon_id="daemon-1",
            source={"kind": "remote-client", "id": "browser-1", "user_id": "user"},
            target={"kind": "daemon", "id": "daemon-1"},
            kind="user_message",
            created_at="2026-05-22T00:00:00.000Z",
            payload={"agent_id": "worker-1", "message": "hello"},
        )

        await connector._handle_envelope(envelope)

        self.assertEqual(calls, [{
            "agent_id": "worker-1",
            "message": "hello",
            "idempotency_key": "msg-remote-1",
        }])
        self.assertEqual(len(fake_ws.sent), 1)
        ack = fake_ws.sent[0]
        self.assertEqual(ack["kind"], "ack")
        self.assertEqual(ack["payload"]["ack_id"], "msg-remote-1")
        self.assertEqual(ack["payload"]["ack_kind"], "user_message")
        self.assertEqual(ack["payload"]["delivery_state"], "acked")

    async def test_agent_messages_and_ask_mirrors_are_queued_outbound(self):
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"},
            remote_user_agent_message=lambda _payload: {"type": "ok"},
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        connector.started = True

        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-agent-1",
                "thread_id": "user-agent:user:worker-1",
                "sender_id": "worker-1",
                "sender_kind": "worker",
                "sender_name": "Worker",
                "recipient_id": "user",
                "recipient_kind": "user",
                "message": "done",
                "message_type": "message",
                "created_at": 1779480000.0,
                "delivery_state": "delivered",
            },
        })
        agent_envelope = await connector._outbound_queue.get()
        self.assertEqual(agent_envelope["kind"], "agent_message")
        self.assertEqual(agent_envelope["id"], "dm:msg-agent-1:agent_message")
        self.assertEqual(agent_envelope["source"], {"kind": "daemon", "id": "daemon-1"})
        self.assertEqual(agent_envelope["payload"]["agent_id"], "worker-1")
        self.assertEqual(agent_envelope["payload"]["message"], "done")

        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-ask-1",
                "thread_id": "user-agent:user:worker-1",
                "sender_id": "worker-1",
                "sender_kind": "worker",
                "recipient_id": "user",
                "recipient_kind": "user",
                "message": "approve?",
                "message_type": "ask",
                "created_at": 1779480001.0,
                "blocking": True,
            },
        })
        ask_envelope = await connector._outbound_queue.get()
        self.assertEqual(ask_envelope["kind"], "ask")
        self.assertEqual(ask_envelope["payload"]["blocking"], True)

    async def test_user_to_agent_direct_messages_do_not_loop_outbound(self):
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"},
            remote_user_agent_message=lambda _payload: {"type": "ok"},
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        connector.started = True

        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-user-1",
                "thread_id": "user-agent:user:worker-1",
                "sender_id": "user",
                "sender_kind": "user",
                "recipient_id": "worker-1",
                "recipient_kind": "worker",
                "message": "status?",
                "message_type": "message",
                "created_at": 1779480002.0,
            },
        })
        self.assertTrue(connector._outbound_queue.empty())


class EnterpriseConnectorSyncTests(unittest.TestCase):
    def test_create_connector_is_inert_until_start(self):
        connector = EnterpriseConnector(context={"config": {}})
        self.assertFalse(connector.started)
        asyncio.run(connector.on_direct_message({"type": "direct_message_saved"}))
        self.assertEqual(connector.observed_events, ["direct_message_saved"])


if __name__ == "__main__":
    unittest.main()
