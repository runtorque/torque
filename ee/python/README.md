# Torque EE Python connector

Enterprise-only Python package boundary for the Channels outbound relay connector.

The open-core daemon loads this package only when explicitly enabled through
`torque.cloud_hooks`; community packaging still excludes `ee/`.

## Phase 3 config surface

Default is **off**. For local development against the standalone relay only:

```sh
TORQUE_EE_CONNECTOR_ENABLED=1 \
TORQUE_EE_RELAY_URL=http://127.0.0.1:8787 \
TORQUE_EE_DAEMON_ID=desktop-dev \
PYTHONPATH=/path/to/torque/ee/python:$PYTHONPATH \
python3 torque.py
```

`TORQUE_CLOUD_RELAY_URL` / `TORQUE_CLOUD_DAEMON_ID` are accepted aliases for the
core seam. Phase 3 is intentionally unauthenticated and rejects non-loopback
relay hosts; authenticated remote owner attach is Phase 4.

## Handshake shape

The connector opens an outbound WebSocket to:

```text
/v1/daemon/:daemon_id/ws
```

Then sends a V1 `hello` envelope. The standalone relay replies with `ready` and
an epoch. Relayed `user_message` envelopes are delivered through the existing
local `user_agent_message` command path and acknowledged with `ack`. Local
direct-message observer rows are emitted as `agent_message`, `ask`, or
`ask_reply` envelopes.
