import assert from "node:assert/strict";
import test from "node:test";

import {
  RELAY_MESSAGE_KINDS,
  RelayProtocolError,
  makeAckEnvelope,
  makeRelayEnvelope,
  parseRelayEnvelope,
} from "../src/core/protocol.js";

test("wire protocol exposes the full V1 message kind contract", () => {
  assert.deepEqual(RELAY_MESSAGE_KINDS, [
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
  ]);
});

test("parseRelayEnvelope validates version, kind, endpoints, JSON payload and timestamps", () => {
  const envelope = makeRelayEnvelope({
    id: "msg-protocol",
    daemon_id: "daemon-1",
    source: { kind: "remote-client", id: "browser-1", user_id: "user-1" },
    target: { kind: "daemon", id: "daemon-1" },
    kind: "user_message",
    created_at: "2026-05-22T00:00:00.000Z",
    payload: { message: "hello", nested: { ok: true } },
  });
  assert.equal(parseRelayEnvelope(envelope).id, "msg-protocol");

  assert.throws(
    () => parseRelayEnvelope({ ...envelope, v: 999 }),
    RelayProtocolError,
  );
  assert.throws(
    () => parseRelayEnvelope({ ...envelope, kind: "bogus" }),
    RelayProtocolError,
  );
  assert.throws(
    () => parseRelayEnvelope({ ...envelope, target: { kind: "daemon", id: "other" } }),
    RelayProtocolError,
  );
  assert.throws(
    () => parseRelayEnvelope({ ...envelope, created_at: "not-a-date" }),
    RelayProtocolError,
  );
  assert.throws(
    () => parseRelayEnvelope({ ...envelope, payload: [] }),
    RelayProtocolError,
  );
});

test("ack envelopes require payload.ack_id", () => {
  const ack = makeAckEnvelope({
    id: "ack-1",
    daemon_id: "daemon-1",
    source: { kind: "daemon", id: "daemon-1" },
    target: { kind: "relay", id: "relay" },
    ack_id: "msg-1",
    ack_kind: "user_message",
    created_at: "2026-05-22T00:00:01.000Z",
  });
  assert.equal(ack.kind, "ack");
  assert.equal(ack.payload.ack_id, "msg-1");

  assert.throws(
    () => parseRelayEnvelope({ ...ack, payload: {} }),
    RelayProtocolError,
  );
});
