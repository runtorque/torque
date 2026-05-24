# Torque EE frontend skeleton

> License boundary: this EE directory is proprietary and is not covered by the repository root MIT License. See [../LICENSE](../LICENSE).

Enterprise-only remote frontend assets live here. Community builds do not copy
`ee/`, and `webview.html` ships with an empty `#torque-ee-frontend-manifest` so
no remote UI appears unless enterprise packaging injects a manifest before
`static/js/panel_manager.js` runs.

Future Panelsmith work can register no-build panel roots through a manifest like:

```json
{
  "version": 1,
  "panels": [
    {
      "id": "remote",
      "title": "Remote",
      "root_id": "panel-remote",
      "default_zone": "right"
    }
  ]
}
```

## `remote/` — Channels P6 remote web UI (user↔agent conversation, V1)

`remote/` is a self-contained, no-build static web app (vanilla JS, one CSS
file) for the **USER↔AGENT conversation lane**. It is a pure *client* of the
shipped relay contract (`ee/relay`) + outbound connector (`ee/python`); it makes
**no** changes to `torque/`, `ee/relay/`, or `ee/python/`. It is loaded on a
remote device (phone/laptop), NOT injected into the local operator webview — so
it is distinct from the `#torque-ee-frontend-manifest` panel mechanism above.

Modules (load order in `index.html` matters — globals first):

| File | Responsibility |
|---|---|
| `js/config.js` | Bootstrap config (relayBaseUrl, daemonId, auth mode, ask flag); endpoint derivation. |
| `js/markdown.js` | Copied verbatim from `static/js/markdown.js` (escape-first renderer). |
| `js/surface.js` | Copied/trimmed scroll+focus capture/restore from `static/js/render.js`. |
| `js/envelope.js` | Relay wire-envelope build/validate/parse (mirrors `protocol.ts`). |
| `js/store.js` | In-memory switchable conversations; ingest, asks, delivery state, tail cap, snapshot. |
| `js/relay_client.js` | WS connect + reconnect/re-auth state machine, id de-dupe, outbound queue, acks. |
| `js/render_remote.js` | Agent list / conversation / ask cards / banner builders + paint discipline. |
| `js/app.js` | DOM glue (browser only). |

Key behaviors (per the approved plan `REMOTE_WEB_UI_PLAN.md` + the locked V1 shape):

- **Session is consumed, never minted.** Cookie-mode (HttpOnly `torque_session`)
  is primary; bearer/`?token=` is local-dev only. The QR/provisioning step hands
  the session to the device.
- **Close codes:** `4003` ⇒ terminal `reauth_required` (re-pair, no tight-retry);
  every other close ⇒ reconnect-with-backoff on the same session.
- **De-dupe** inbound by envelope id (bounded LRU) + store upsert by message id;
  per-conversation tail cap. At-least-once delivery, never exactly-once.
- **Ask UI is ON by default** (`ask_enabled`, P6-V1 completion) now that the
  canonical user-destination gating (TORQUE:534) is live. The UI **never**
  filters ask recipients itself — user-destination is resolved server-side; it
  renders/answers the already-resolved user-destined stream. Ask answers are
  sent as a `user_message` carrying `reply_to_id` (the connector ingress accepts
  only `user_message`). Set `ask_enabled=0` to opt out.
- **Recent history on open** (TORQUE:578): the client sends a `snapshot_request`
  on connect (after `ready`); the connector replies with one `snapshot` envelope
  whose `payload.messages[]` rows are the live payload shape + a top-level `kind`
  (agent_message/ask/ask_reply), oldest-first, already user-destination-gated.
  Rows render via the **exact same** path as live messages (no separate snapshot
  renderer) and de-dupe against the live stream by message id. A still-pending
  snapshot `ask` stays answerable; an ask answered before connect (ask+ask_reply
  both present) renders resolved. Degrades gracefully (replay-only "showing new
  messages" notice) when no/empty snapshot arrives.

### Local end-to-end loopback recipe (no remote exposure)

The full UI ↔ relay ↔ connector ↔ daemon round-trip is a manual loopback
verification (it needs the TS relay built + the Python daemon/connector running;
it is not part of `make test`). Go-live remains a separate user gate.

```sh
# 1. Standalone relay (local-dev-unauthenticated, loopback). Requires a one-time
#    `npm install` in ee/relay; `npm run dev` builds (tsc) then serves.
cd ee/relay && TORQUE_RELAY_DB=/tmp/relay.db npm run dev   # 127.0.0.1:8787

# 2. Run the daemon with the EE connector pointed at the loopback relay
#    (TORQUE_CLOUD_CONNECTOR_ENABLED=true + connector relay_url=ws://127.0.0.1:8787,
#    daemon_id=<id>) so agent_message/ask egress + snapshot_request handling are live.

# 3. Serve the bundle with any static server:
cd ee/frontend/remote && python3 -m http.server 8080

# 4. Open with config via URL params (local dev, no session needed):
#    http://127.0.0.1:8080/?relayBaseUrl=http://127.0.0.1:8787&daemonId=<id>
#    (ask UI is on by default; append &ask=0 to disable, &snapshot_limit=50 to cap)
```

Manual round-trips to confirm: (a) on open, recent history renders oldest-first
from the snapshot; (b) a message that is both in the snapshot and arrives live
shows once; (c) an agent `ask` renders an answerable card and the reply reaches
the agent; (d) a typed `user_message` reaches the agent and its `agent_message`
reply renders back.

### Verification status (P6-V1)

- **Verified via the Node suite (injected/emulated relay):** snapshot_request is
  sent on `ready`; `ingestSnapshot` consumes the real shipped row shape
  oldest-first and de-dupes vs live by id; a snapshot ask is actionable without
  bumping unread; ask answers ride on `user_message`+`reply_to_id`; the full
  client pipeline (`tests/frontend_remote_e2e.test.js`) drives snapshot/ask/
  message round-trips over a fake socket that replays the documented connector
  contract.
- **Requires the manual loopback recipe above (not automatable in `node --test`):**
  the live full-stack round-trip through the real TypeScript relay + Python
  daemon/connector. The connector snapshot egress is a daemon component (needs a
  live `recent_direct_messages` provider), so it cannot run inside the frontend
  test harness.

Node regression tests live in `tests/frontend_remote_*.test.js` (run via
`tests/test_frontend_remote.py` in the Python suite).
