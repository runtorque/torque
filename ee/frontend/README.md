# Torque EE frontend skeleton

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
- **Ask UI is feature-flagged off** (`ask_enabled`) until TORQUE:534 + the
  connector user-destination egress gating land. The UI **never** filters ask
  recipients itself — user-destination is resolved server-side; the flag only
  toggles whether the already-resolved user-destined ask stream is rendered/
  answerable. Ask answers are sent as a `user_message` carrying `reply_to_id`
  (the connector ingress accepts only `user_message`).
- **Recent history on open** via a `snapshot` envelope (TORQUE:578), applied as a
  batch before live messages through the same de-dupe/upsert; degrades
  gracefully to the replay-only "showing new messages" notice when no/empty
  snapshot arrives.

### Local test recipe (no remote exposure)

```sh
# 1. Standalone relay (local-dev-unauthenticated, loopback):
cd ee/relay && TORQUE_RELAY_DB=/tmp/relay.db npm run dev   # 127.0.0.1:8787

# 2. Serve the bundle with any static server, e.g.:
cd ee/frontend/remote && python3 -m http.server 8080

# 3. Open with config via URL params (local dev, no session needed):
#    http://127.0.0.1:8080/?relayBaseUrl=http://127.0.0.1:8787&daemonId=<id>&ask=1
```

Node regression tests live in `tests/frontend_remote_*.test.js` (run via
`tests/test_frontend_remote.py` in the Python suite).
