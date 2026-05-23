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
    CONNECTOR_DEBUG_BUFFER_LIMIT,
    SNAPSHOT_DEFAULT_MESSAGE_LIMIT,
    EnterpriseConnector,
    _agent_id_for_direct_message,
    _direct_message_payload,
    _wire_kind_for_direct_message_row,
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

    async def test_user_message_ingress_error_emits_remote_error_and_failed_ack(self):
        async def remote_ingress(_payload):
            return {
                "type": "error",
                "code": "agent_not_found",
                "message": "agent worker-404 not found",
                "retryable": False,
            }

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
            id="msg-remote-error",
            daemon_id="daemon-1",
            source={"kind": "remote-client", "id": "browser-1", "user_id": "user"},
            target={"kind": "daemon", "id": "daemon-1"},
            kind="user_message",
            created_at="2026-05-22T00:00:00.000Z",
            payload={"agent_id": "worker-404", "message": "hello"},
        )

        await connector._handle_envelope(envelope)

        self.assertEqual(len(fake_ws.sent), 2)
        ack, error = fake_ws.sent
        self.assertEqual(ack["kind"], "ack")
        self.assertEqual(ack["payload"]["ack_id"], "msg-remote-error")
        self.assertEqual(ack["payload"]["ack_kind"], "user_message")
        self.assertEqual(ack["payload"]["delivery_state"], "failed")
        self.assertEqual(ack["payload"]["reason"], "agent worker-404 not found")
        self.assertEqual(error["kind"], "error")
        self.assertEqual(error["target"], {"kind": "remote-client", "id": "browser-1", "user_id": "user"})
        self.assertEqual(error["payload"]["code"], "agent_not_found")
        self.assertEqual(error["payload"]["message"], "agent worker-404 not found")
        self.assertEqual(error["payload"]["ref_id"], "msg-remote-error")
        self.assertIs(error["payload"]["retryable"], False)

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

    async def test_owner_routed_asks_do_not_egress_to_user_lane(self):
        # Consumer #3 of the canonical resolver: only user-destined asks may
        # reach the {remote-client, user} lane.  An owner-routed ask
        # (recipient=engineer) must NOT egress.
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"},
            remote_user_agent_message=lambda _payload: {"type": "ok"},
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        connector.started = True

        # Engineer-owned worker's ask: recipient is the engineer, not the user.
        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-ask-owner",
                "thread_id": "user-agent:user:worker-1",
                "sender_id": "worker-1",
                "sender_kind": "worker",
                "recipient_id": "eng-1",
                "recipient_kind": "engineer",
                "message": "approve?",
                "message_type": "ask",
                "created_at": 1779480001.0,
                "blocking": True,
            },
        })
        self.assertTrue(connector._outbound_queue.empty())

        # Reply to an owner-routed ask: answerer (sender) is the engineer.
        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-ask-owner-reply",
                "thread_id": "user-agent:user:worker-1",
                "sender_id": "eng-1",
                "sender_kind": "engineer",
                "recipient_id": "worker-1",
                "recipient_kind": "worker",
                "message": "go ahead",
                "message_type": "ask_reply",
                "created_at": 1779480002.0,
            },
        })
        self.assertTrue(connector._outbound_queue.empty())

        # A genuinely user-destined ask + its user reply DO egress.
        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-ask-user",
                "thread_id": "user-agent:user:arch-1",
                "sender_id": "arch-1",
                "sender_kind": "architect",
                "recipient_id": "user",
                "recipient_kind": "user",
                "message": "ship it?",
                "message_type": "ask",
                "created_at": 1779480003.0,
                "blocking": True,
            },
        })
        ask_envelope = await connector._outbound_queue.get()
        self.assertEqual(ask_envelope["kind"], "ask")

        await connector.on_direct_message({
            "type": "direct_message_saved",
            "row": {
                "id": "msg-ask-user-reply",
                "thread_id": "user-agent:user:arch-1",
                "sender_id": "user",
                "sender_kind": "user",
                "recipient_id": "arch-1",
                "recipient_kind": "architect",
                "message": "yes",
                "message_type": "ask_reply",
                "created_at": 1779480004.0,
            },
        })
        reply_envelope = await connector._outbound_queue.get()
        self.assertEqual(reply_envelope["kind"], "ask_reply")
        self.assertTrue(connector._outbound_queue.empty())

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

    async def test_debug_envelope_lists_are_bounded_ring_buffers(self):
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config={"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"},
            remote_user_agent_message=lambda _payload: {"type": "ok"},
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        fake_ws = FakeWs()
        connector._ws = fake_ws

        for index in range(CONNECTOR_DEBUG_BUFFER_LIMIT + 25):
            event_id = f"event-{index}"
            await connector.on_direct_message({"type": event_id})
            await connector._handle_envelope(make_relay_envelope(
                id=f"ready-{index}",
                daemon_id="daemon-1",
                source={"kind": "relay", "id": "relay"},
                target={"kind": "daemon", "id": "daemon-1"},
                kind="ready",
                created_at="2026-05-22T00:00:00.000Z",
                payload={"epoch": index},
            ))
            await connector._send_envelope(make_relay_envelope(
                id=f"ping-{index}",
                daemon_id="daemon-1",
                source={"kind": "daemon", "id": "daemon-1"},
                target={"kind": "relay", "id": "relay"},
                kind="ping",
                created_at="2026-05-22T00:00:00.000Z",
                payload={},
            ))

        self.assertEqual(len(connector.observed_events), CONNECTOR_DEBUG_BUFFER_LIMIT)
        self.assertEqual(len(connector.received_envelopes), CONNECTOR_DEBUG_BUFFER_LIMIT)
        self.assertEqual(len(connector.sent_envelopes), CONNECTOR_DEBUG_BUFFER_LIMIT)
        self.assertEqual(connector.observed_events[0], "event-25")
        self.assertEqual(connector.received_envelopes[0], "ready-25")
        self.assertEqual(connector.sent_envelopes[0], "ping-25")


class SnapshotRequestTests(unittest.IsolatedAsyncioTestCase):
    """snapshot_request → bounded user↔agent snapshot, gated like live egress."""

    def _connector(self, *, rows=None, recent_provider=None, config=None):
        cfg = {"relay_url": "http://127.0.0.1:8787", "daemon_id": "daemon-1"}
        if config:
            cfg.update(config)
        provider = recent_provider
        if provider is None and rows is not None:
            provider = lambda _limit: [dict(r) for r in rows]  # noqa: E731
        context = SimpleNamespace(
            profile="desktop",
            data_dir="/tmp/torque-desktop",
            config=cfg,
            remote_user_agent_message=lambda _payload: {"type": "ok"},
            recent_direct_messages=provider,
        )
        connector = EnterpriseConnector(context=context)
        connector.config = config_from_context(context)
        connector.started = True
        connector._ws = FakeWs()
        return connector

    def _request(self, **payload):
        return make_relay_envelope(
            id="snap-req-1",
            daemon_id="daemon-1",
            source={"kind": "remote-client", "id": "user", "user_id": "user"},
            target={"kind": "daemon", "id": "daemon-1"},
            kind="snapshot_request",
            created_at="2026-05-23T00:00:00.000Z",
            payload=payload,
        )

    async def _snapshot_payload(self, connector, request):
        await connector._handle_envelope(request)
        sent = [e for e in connector._ws.sent if e.get("kind") == "snapshot"]
        self.assertEqual(len(sent), 1, "expected exactly one snapshot envelope")
        return sent[0]

    def _lane_rows(self):
        # Newest-first, as the daemon provider returns them.
        return [
            {  # user answered an ask -> ask_reply egresses
                "id": "r1", "message_type": "ask_reply",
                "sender_id": "user", "sender_kind": "user",
                "recipient_id": "w1", "recipient_kind": "worker",
                "recipient_name": "Worker One", "message": "go ahead",
                "group_name": "g", "created_at": 105.0,
            },
            {  # owner-routed ask (recipient=engineer) -> MUST be excluded
                "id": "r2", "message_type": "ask",
                "sender_id": "w2", "sender_kind": "worker",
                "recipient_id": "e1", "recipient_kind": "engineer",
                "message": "approve?", "group_name": "g",
                "created_at": 104.0, "blocking": True,
            },
            {  # user-destined ask -> ask egresses
                "id": "r3", "message_type": "ask",
                "sender_id": "w1", "sender_kind": "worker",
                "recipient_id": "user", "recipient_kind": "user",
                "message": "ship it?", "group_name": "g",
                "created_at": 103.0, "blocking": True,
            },
            {  # agent->user message -> agent_message egresses
                "id": "r4", "message_type": "message",
                "sender_id": "w1", "sender_kind": "worker",
                "sender_name": "Worker One",
                "recipient_id": "user", "recipient_kind": "user",
                "message": "done", "group_name": "g", "created_at": 102.0,
            },
            {  # user->agent message -> not looped back outbound
                "id": "r5", "message_type": "message",
                "sender_id": "user", "sender_kind": "user",
                "recipient_id": "w1", "recipient_kind": "worker",
                "message": "status?", "group_name": "g", "created_at": 101.0,
            },
        ]

    async def test_snapshot_is_user_agent_only_and_ask_gated(self):
        connector = self._connector(rows=self._lane_rows())
        payload = await self._snapshot_payload(connector, self._request())

        self.assertEqual(payload["payload"]["lane"], "user-agent")
        msgs = payload["payload"]["messages"]
        # r2 (owner-routed ask) and r5 (user->agent) excluded; oldest-first.
        self.assertEqual(
            [(m["message_id"], m["kind"]) for m in msgs],
            [("r4", "agent_message"), ("r3", "ask"), ("r1", "ask_reply")],
        )
        self.assertEqual(payload["payload"]["count"], 3)
        # Targets the requesting remote client.
        self.assertEqual(payload["target"]["kind"], "remote-client")
        self.assertEqual(payload["target"]["id"], "user")

    async def test_snapshot_row_payload_matches_live_agent_message(self):
        rows = self._lane_rows()
        connector = self._connector(rows=rows)
        payload = await self._snapshot_payload(connector, self._request())
        entry = next(m for m in payload["payload"]["messages"] if m["message_id"] == "r4")
        # The agent_message row -> identical payload shape to live egress + kind.
        live = _direct_message_payload(rows[3], _agent_id_for_direct_message(rows[3]))
        self.assertEqual(entry["kind"], "agent_message")
        self.assertEqual({k: v for k, v in entry.items() if k != "kind"}, live)

    async def test_snapshot_is_bounded_by_default_cap(self):
        many = [
            {
                "id": f"m{i}", "message_type": "message",
                "sender_id": "w1", "sender_kind": "worker",
                "recipient_id": "user", "recipient_kind": "user",
                "message": f"msg {i}", "group_name": "g",
                "created_at": float(1000 + i),
            }
            for i in range(SNAPSHOT_DEFAULT_MESSAGE_LIMIT + 150)
        ]
        # Provider receives the cap as its limit; emulate it returning the cap.
        captured = {}

        def provider(limit):
            captured["limit"] = limit
            return [dict(r) for r in many[: limit]]

        connector = self._connector(recent_provider=provider)
        payload = await self._snapshot_payload(connector, self._request())
        self.assertEqual(captured["limit"], SNAPSHOT_DEFAULT_MESSAGE_LIMIT)
        self.assertEqual(payload["payload"]["count"], SNAPSHOT_DEFAULT_MESSAGE_LIMIT)
        self.assertEqual(len(payload["payload"]["messages"]), SNAPSHOT_DEFAULT_MESSAGE_LIMIT)
        self.assertTrue(payload["payload"]["truncated"])

    async def test_snapshot_honors_smaller_requested_limit(self):
        captured = {}

        def provider(limit):
            captured["limit"] = limit
            return []

        connector = self._connector(recent_provider=provider)
        await self._snapshot_payload(connector, self._request(limit=5))
        self.assertEqual(captured["limit"], 5)

    async def test_requested_limit_cannot_exceed_cap(self):
        captured = {}

        def provider(limit):
            captured["limit"] = limit
            return []

        connector = self._connector(recent_provider=provider)
        await self._snapshot_payload(
            connector, self._request(limit=SNAPSHOT_DEFAULT_MESSAGE_LIMIT + 5000)
        )
        self.assertEqual(captured["limit"], SNAPSHOT_DEFAULT_MESSAGE_LIMIT)

    async def test_snapshot_empty_when_no_provider(self):
        connector = self._connector(recent_provider=None)
        payload = await self._snapshot_payload(connector, self._request())
        self.assertEqual(payload["payload"]["count"], 0)
        self.assertEqual(payload["payload"]["messages"], [])
        self.assertFalse(payload["payload"]["truncated"])


class WireKindGateTests(unittest.TestCase):
    """The egress gate consumes the resolver-stamped row fields only."""

    def _row(self, **overrides):
        row = {
            "message_type": "message",
            "sender_kind": "worker",
            "sender_id": "worker-1",
            "recipient_kind": "user",
            "recipient_id": "user",
        }
        row.update(overrides)
        return row

    def test_user_destined_ask_egresses(self):
        self.assertEqual(
            _wire_kind_for_direct_message_row(
                self._row(message_type="ask")
            ),
            "ask",
        )

    def test_owner_routed_ask_is_suppressed(self):
        for recipient_kind, recipient_id in (
            ("engineer", "eng-1"),
            ("architect", "arch-1"),
            ("user", ""),  # malformed: kind without canonical id
        ):
            self.assertEqual(
                _wire_kind_for_direct_message_row(
                    self._row(
                        message_type="ask",
                        recipient_kind=recipient_kind,
                        recipient_id=recipient_id,
                    )
                ),
                "",
                (recipient_kind, recipient_id),
            )

    def test_ask_reply_egresses_only_when_user_answered(self):
        # Reply mirror swaps sender/recipient: the answerer is the sender.
        self.assertEqual(
            _wire_kind_for_direct_message_row(
                self._row(
                    message_type="ask_reply",
                    sender_kind="user",
                    sender_id="user",
                    recipient_kind="architect",
                    recipient_id="arch-1",
                )
            ),
            "ask_reply",
        )
        self.assertEqual(
            _wire_kind_for_direct_message_row(
                self._row(
                    message_type="ask_reply",
                    sender_kind="engineer",
                    sender_id="eng-1",
                    recipient_kind="worker",
                    recipient_id="worker-1",
                )
            ),
            "",
        )

    def test_agent_message_gate_unchanged(self):
        self.assertEqual(
            _wire_kind_for_direct_message_row(
                self._row(message_type="message")
            ),
            "agent_message",
        )
        self.assertEqual(
            _wire_kind_for_direct_message_row(
                self._row(
                    message_type="message",
                    sender_kind="user",
                    sender_id="user",
                    recipient_kind="worker",
                    recipient_id="worker-1",
                )
            ),
            "",
        )


class EnterpriseConnectorSyncTests(unittest.TestCase):
    def test_create_connector_is_inert_until_start(self):
        connector = EnterpriseConnector(context={"config": {}})
        self.assertFalse(connector.started)
        asyncio.run(connector.on_direct_message({"type": "direct_message_saved"}))
        self.assertEqual(connector.observed_events, ["direct_message_saved"])


if __name__ == "__main__":
    unittest.main()
