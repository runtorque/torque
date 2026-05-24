# Torque EE Python connector

> License boundary: this EE directory is proprietary and is not covered by the repository root MIT License. See [../LICENSE](../LICENSE).

Enterprise-only Python package boundary for the Channels outbound relay connector.

The open-core daemon loads this package only when explicitly enabled through
`torque.cloud_hooks`; community packaging still excludes `ee/`.

## Phase 4 config surface

Default is **off**. Loopback standalone development can still run without a
credential:

```sh
TORQUE_EE_CONNECTOR_ENABLED=1 \
TORQUE_EE_RELAY_URL=http://127.0.0.1:8787 \
TORQUE_EE_DAEMON_ID=desktop-dev \
PYTHONPATH=/path/to/torque/ee/python:$PYTHONPATH \
python3 torque.py
```

Reachable/non-loopback relay URLs require signed daemon attach:

- `TORQUE_EE_DAEMON_CREDENTIAL_ID` or `credential_id` in the profile config;
- `TORQUE_EE_DAEMON_PRIVATE_KEY_PEM` or `private_key_pem`/`private_key_path` in
  `~/.torque/profiles/<profile>/ee_connector.json`;
- private-key/config files must be mode `0600`;
- remote URLs must resolve to `wss://` after normalization.

The connector signs the WebSocket attach request with ES256/P-256. The private
key never leaves the local profile. The relay stores only the public key created
during pairing.

`cryptography` is an EE-only dependency. It is imported lazily: environments
without it can still import the connector package, and signed remote attach is
cleanly disabled instead of crashing at import time.

## Handshake shape

The connector opens an outbound WebSocket to:

```text
/v1/daemon/:daemon_id/ws
```

For signed mode the HTTP upgrade carries `Authorization: Torque-Daemon-Signature
v1 ...`. After the relay verifies the signature, owner, timestamp, and nonce, it
returns `ready` with an epoch. Relayed `user_message` envelopes are delivered
through the existing local `user_agent_message` command path and acknowledged
with `ack`. Local direct-message observer rows are emitted as `agent_message`,
`ask`, or `ask_reply` envelopes.
