# Channels P6: Remote Web UI — User↔Agent Conversation Channel (V1)

**Task:** TORQUE:573 · **Type:** Research/scoping → implementation plan · **Status:** plan-only (no code written)

**Phase:** P6, first usable Channel. Build + test LOCALLY against the standalone relay. **No remote exposure** in this phase; go-live is a separate user gate. **Mandatory review on the eventual implementation.**

---

## 0. Contract basis & cross-check (code is ground truth)

The contract was derived from the **shipped, merged** `ee/relay/` (TypeScript) + `ee/python/torque_ee_connector/` (Python) code and **cross-checked** against:

- Courier's published shared-memory contract `5a0411e2110d` ("P6/P7 Channels remote-client CONTRACT", pinned, group scope) — **matches the code** on wire protocol, client endpoints, auth, and connector ingress/egress. (Its section 5 text is itself truncated mid-sentence in the store; close-code details below come from code, not the memo.)
- Design doc `ee/relay/docs/channels-remote-torque-design.md` (= TORQUE:544 attachment) — reconciled; no material divergence beyond the close-code framing below.

### Divergences / gaps to FLAG (engineer to confirm before implementation)

1. **Close-code semantics — client vs daemon (DIVERGENCE from task framing).** The task essentials say the client should "expect WS close 4003 (session revoked) / 4001 (stale) on owner-change/takeover." In the **shipped code**, the *client* socket is closed with **4003 `authenticated_session_revoked`** on owner-change / session-revoke / expiry (`redisCoordinator.ts:620,625`, `durableObjectCoordinator.ts:229`) and **4003 `client_attach_rejected`** on a rejected attach (`durableObjectCoordinator.ts:215`). **4001 `stale_daemon_connection`** (`redisCoordinator.ts:712`, `durableObjectCoordinator.ts:93`) and **4000 `replaced_by_new_daemon_connection`** (`registryCoordinator.ts:57`) are **daemon-socket** closes — a client does **not** normally receive 4001/4000. **Resolution for the UI:** treat **4003 ⇒ session is dead ⇒ re-auth required (re-provision)**, and treat **all other closes (incl. 4001/4000/1011/1006/normal) ⇒ transient ⇒ reconnect-with-backoff using the existing session.** Handle 4001/4000 defensively anyway (close codes are advisory and adapters may evolve), but only 4003 forces re-auth. This keeps us correct under both standalone-redis and Cloudflare adapters.

2. **No conversation-history snapshot on connect (GAP).** The relay does **not** send a full conversation snapshot to a client on attach. On client attach it **replays up to `replayLimit` (default 100) *pending* (un-acked) from-daemon messages** (`runtime.ts:203-220`, surfaced as `replayed`/`replay_failed` in the `ready` payload). The connector does **not** implement `snapshot`/`snapshot_request` egress (confirmed gap in `connector.py`). **Consequence:** a freshly-opened remote UI sees only (a) replayed pending messages + (b) messages from connect-time forward. It will **not** show already-acked history. This is a **product-behavior consequence of the locked contract**, not a new decision — see §11 for the recommended V1 stance and an optional, contract-compatible follow-up.

3. **No agent roster in V1 (scope boundary, consistent with "no board-state sync").** There is no client endpoint that lists agents. The switchable agent list must be **derived from observed conversation traffic** (`payload.agent_id` / `agent_name` on inbound `agent_message`/`ask`). The user can reply to / switch among agents that have messaged; **initiating a brand-new conversation to an agent that has never messaged is out of V1 scope** (would need a roster channel). See §8.

4. **Single-process standalone does not force-close clients on owner change.** Only the **redis** standalone coordinator and the **Cloudflare** DO close client sockets with 4003; the default in-process `StandaloneRegistryCoordinator` has no client-close path (only the 4000 daemon replace). **Test implication:** to exercise the 4003 re-auth path locally you must run the standalone relay in **redis coordination mode** (`TORQUE_RELAY_COORDINATION=redis`) or unit-test the client state machine directly. See §12.

5. **Connector egress does NOT yet hand a clean *user-destined* ask stream (CONTRACT GAP — route to Courier, depends on TORQUE:534).** Per the architect's P6 constraint, the remote UI must **consume** an already-resolved user-destined ask stream and must **not** reimplement ask-recipient filtering client-side. Code reconnaissance shows the connector egress filter `_wire_kind_for_direct_message_row` (`ee/python/torque_ee_connector/connector.py:633-645`) gates **`message`** on `sender_kind != "user" AND recipient_kind == "user"` (line 643) but forwards **`ask`/`ask_reply` UNCONDITIONALLY** (lines 639-642) — *no* `recipient_kind == "user"` gate. Combined with the owner-aware ask-mirror recipient (shared memory `62395dcaa2e7`: ask-mirror rows carry the resolved owner-chain recipient — `engineer`/`architect`, not necessarily `user`), the connector would currently forward **non-user-destined asks** (e.g. worker→engineer, engineer→architect) onto the `{id:"user"}` remote-client lane. **This means the V1 remote-ask surface depends on TORQUE:534** establishing the canonical user-destined resolver **AND** a connector-egress change to gate `ask`/`ask_reply` on user-destination (the `recipient==user` helper from S4 / :530 / :553), mirroring how `agent_message` is already gated. **Until that lands, there is no clean user-destined ask stream to consume.** Note: shared-memory `decision-ed181c04bd1d` (the resolver detail) was **not found** in the store at plan time (:534 dispatch in flight) — pending cross-check. **Action: flag to Courier; the remote UI does not work around it client-side.**

---

## 1. Verified contract (the parts the UI depends on)

### Wire envelope (`ee/relay/src/core/protocol.ts`)
```
{ v:1, id, trace_id?, daemon_id, source, target, kind, created_at(ISO-8601), payload:{} }
endpoint = { kind:"daemon"|"remote-client"|"channel"|"relay", id, user_id?, platform? }
```
- `RELAY_PROTOCOL_VERSION = 1`; every envelope MUST set `v:1`.
- Frontend-relevant kinds: **`user_message`** (UI→agent), **`agent_message`** (agent→UI), **`ask`** (agent→UI), **`ask_reply`** (UI→agent). Control: `ready`, `ack`, `error`, `ping`/`pong`, (`snapshot_request`/`snapshot` exist but connector does not implement them).
- `ack` payload: `{ack_id, ack_kind?, delivery_state?:"delivered"|"acked"|"failed", reason?}`.
- `error` payload: `{code, message, retryable?, ref_id?}`.
- Validation: `created_at` ISO-8601; payload must be a **JSON object** (null/primitive rejected, PR #59); daemon-kind endpoints' `id` must equal `daemon_id`.
- **Relay forces `source.user_id="user"`** on every client envelope (`auth.ts:243` `sanitizeClientEnvelopeForV1`, const `SYNTHETIC_V1_USER_ID="user"`). The UI cannot and need not set it.

### Client endpoints (identical across standalone `server.ts` + Cloudflare `worker.ts`)
- **WS:** `GET /v1/client/{daemonId}/ws?client_id=<optional>` → relay sends a `ready` control envelope on attach (payload includes `epoch`, `connectionId`, `replayed`, `replay_failed`).
- **HTTP send:** `POST /v1/messages/{daemonId}`, body = relay envelope → `202 {delivered, idempotent, message, delivery}`. Path `daemonId` must equal `envelope.daemon_id` else `400`.
- **Health:** `GET /health` → `{coordination, protocol_version, auth_mode, ...}`.

### Client auth (`ee/relay/src/core/auth.ts` `authenticateClientSession`)
- Credential = **client session token**, presented as `Authorization: Bearer <token>` **OR** `Cookie: torque_session=<token>`.
- Server: token → SHA-256 hash → lookup → must be active (not revoked, not expired) → session `owner_user_id` must match the paired daemon instance owner. Failure codes: `missing_client_auth`(401), `invalid_client_session`(401), `client_owner_mismatch`(403).
- Sessions are **operator/admin-provisioned**: `POST /v1/admin/client-sessions` (bootstrap-token gated) → `{client_session_token, session}`. **This is the QR-provisioning source.** The UI **consumes** a session; it never mints one.
- `local-dev-unauthenticated` mode (loopback only) accepts a client WS/POST with **no** token — used for local testing.

### Delivery semantics
- **At-least-once** with idempotent append by envelope `id` (`sqliteStore.appendMessageResult`, deterministic envelope hash). **The UI MUST de-dupe on `envelope.id`** and never assume exactly-once.
- Message id format: `msg-<uuid>` / `ack-<uuid>` (`protocol.newRelayId`).

### Connector mapping (`ee/python/torque_ee_connector/connector.py`) — already shipped, no changes needed
- **Egress** (daemon→relay→client): local direct-message events of type `{message, ask, ask_reply}` with `sender_kind != "user"` are forwarded as `agent_message`/`ask`/`ask_reply`, target `{kind:"remote-client", id:"user", user_id:"user"}`. Payload mirrors the `agent_peer_messages` row (incl. `agent_id`/sender identity, `message`, `message_type`, `blocking`, `thread_id`, `reply_to_id`, `created_at`).
- **Ingress** (client→relay→daemon): **only `user_message`** accepted → routed to the existing `user_agent_message` path (same store as local typing; payload allowlist: `agent_id, message, thread_id, reply_to_id, idempotency_key`). Other inbound kinds → `unsupported_relay_kind` error. On ingress failure → relay `error` (`ref_id`=orig id) + failed `ack` (PR #59).

---

## 2. Architecture decision: where the remote UI lives & how it loads

**Decision: a self-contained, no-build static web app under `ee/frontend/remote/` (its own `index.html` + a small set of vanilla JS modules + one CSS file), distinct from the local operator webview.** It is a *client of the shipped relay contract* and contains **zero** Torque backend/relay/connector changes.

Rationale / trade-offs considered:
- **Not** an injected `panel_manager` manifest panel. The `ee/frontend` manifest mechanism (README + `manifest.example.json`) injects panels into the **local** `webview.html` operator app. The P6 artifact is a UI loaded on a **remote device** (phone/laptop), mobile-first, consuming a session cookie/token and talking to relay HTTP/WS endpoints — none of which fit the local panel model. The manifest path stays available for any *future local* "remote status" panel but is out of scope here.
- **Lives in `ee/`** ⇒ automatically excluded from community packaging (verified: `scripts/assert_community_package_excludes_ee.py` asserts no `ee` path parts and a fixed top-level allowlist; root `ee/` is never copied into the standalone artifact). No new packaging work required.
- **No-build, vanilla JS** matches repo conventions (no framework, no TypeScript, no bundler) and lets us reuse `markdown.js` and the `render.js` capture/restore helpers verbatim (copied into the bundle — see §10).

**Hosting (deferred to go-live, NOT this phase):** production serving (relay static route vs Cloudflare Pages vs daemon-proxied) is a separate user-gated decision. For V1 we only build the bundle and **test it locally** by serving the `ee/frontend/remote/` directory with any static file server and pointing it at the standalone relay (`127.0.0.1:8787`).

**Bootstrap/config the UI consumes** (provided by the QR/provisioning step, out of UI's minting control):
```
{ relayBaseUrl: "https://relay.example|http://127.0.0.1:8787",
  daemonId: "<daemon id>",
  sessionToken?: "<client_session_token>"   // when Bearer mode; omitted when torque_session cookie is set HttpOnly }
```
The QR code encodes this (relay base + daemonId + session). Two consumption modes, both supported by the relay:
- **Cookie mode (preferred):** the provisioning step sets `torque_session` as an HttpOnly cookie on the relay origin; both the WS upgrade and HTTP POST carry it automatically. The QR then only needs `{relayBaseUrl, daemonId}` + a one-time landing URL that sets the cookie.
- **Bearer mode (local dev / fallback):** UI holds the token in memory (NOT localStorage for the token if avoidable) and sends `Authorization: Bearer`. Browsers cannot set custom headers on a `WebSocket` constructor, so **Bearer-on-WS requires the cookie path OR passing the token via the `Sec-WebSocket-Protocol`/query fallback** — see §6 open question O1.

---

## 3. File plan (all new, all under `ee/frontend/remote/`)

| File | Responsibility |
|---|---|
| `ee/frontend/remote/index.html` | App shell: agent-list pane + conversation pane + composer. Loads modules in dependency order (no bundler). Reads bootstrap config from a `<script id="torque-remote-config">` tag and/or URL params. |
| `ee/frontend/remote/css/remote.css` | Mobile-first single stylesheet, CSS custom properties, monospace — mirrors `static/style.css` conventions. |
| `ee/frontend/remote/js/config.js` | Parse + validate bootstrap config (relayBaseUrl, daemonId, token/cookie mode). Single source of truth for endpoints. |
| `ee/frontend/remote/js/markdown.js` | **Copied verbatim** from `static/js/markdown.js` (standalone IIFE, zero deps). Renders message bodies. |
| `ee/frontend/remote/js/surface.js` | **Copied** `_captureSurfaceState` / `_restoreSurfaceState` (+ scroll-anchor helpers) from `static/js/render.js`, trimmed of Torque-board-specific bits. Preserves scroll/caret/draft across rerenders. |
| `ee/frontend/remote/js/envelope.js` | Build/validate relay envelopes (`v:1`, ids via crypto.randomUUID, ISO `created_at`), construct `user_message` + `ask_reply`, parse inbound, expose envelope `kind`/`id`/`payload` accessors. Mirrors the relevant parts of `protocol.ts`. |
| `ee/frontend/remote/js/relay_client.js` | WS connect + reconnect/re-auth state machine (§6), HTTP POST send path, `ready`/`ack`/`error` handling, ping/pong, **id-dedupe set**, ordered outbound queue with ack tracking. Emits high-level events to the store. |
| `ee/frontend/remote/js/store.js` | In-memory conversation model: `conversationsByAgent`, ordered messages, pending-ask state, connection status. De-dupe + merge. No global mutation; explicit subscribe/notify. |
| `ee/frontend/remote/js/render_remote.js` | Render agent list, active conversation, ask cards, composer; uses `surface.js` for stability and `markdown.js` for bodies. |
| `ee/frontend/remote/js/app.js` | Wire config → relay_client → store → render. Handle composer submit (user_message), ask_reply submit, agent switch, connection-status banner (connecting/reconnecting/re-auth-needed). |
| `ee/frontend/README.md` | **Update** to document the `remote/` app, local-test recipe, and the deferred-hosting note. |

No changes to `torque/`, `ee/relay/`, or `ee/python/`. (If §11 optional snapshot follow-up is approved, that would touch `ee/python/torque_ee_connector/connector.py` — explicitly **out of V1 baseline**.)

---

## 4. Conversation/store model

- `store.conversationsByAgent[agentId] = { agentId, agentName, messages: [...], pendingAsks: Map<askId, askMsg>, lastActivityTs }`.
- A message: `{ id, kind, agentId, sender:"user"|"agent", body, messageType:"message"|"ask"|"ask_reply", blocking, threadId, replyToId, createdAt, deliveryState }`.
- **Inbound** `agent_message`/`ask`/`ask_reply` → resolve `agentId` from payload; upsert into that agent's conversation; if `kind==="ask"` and `blocking`, add to `pendingAsks`. When an `ask_reply` (locally echoed or relayed) resolves an ask, clear it from `pendingAsks`.
- **Outbound** user `user_message`/`ask_reply` → optimistic local append with `deliveryState:"pending"`; update to `"delivered"`/`"acked"`/`"failed"` on matching `ack` (`ack_id` = our envelope `id`) or `error` (`ref_id` = our envelope `id`).
- **De-dupe:** maintain a bounded LRU `Set` of seen envelope ids in `relay_client.js`; drop duplicates before they reach the store (handles at-least-once + replay-on-reconnect overlap).
- **Bound memory:** cap messages per conversation (tail cap, e.g. last 500) consistent with the existing `direct_messages_by_agent` tail-cap pattern noted in shared memory; never hold unbounded history.

---

## 5. Auth / session consumption

- `config.js` determines mode: cookie (no token in config) vs Bearer (token in config / URL fragment).
- HTTP POST: attach `Authorization: Bearer` header (Bearer mode) — cookie mode needs nothing (browser sends `torque_session`).
- WS: see §6 O1 for the header-on-WS constraint.
- **Re-auth on 4003:** the UI cannot mint a session. On 4003 it transitions to a terminal **"session expired — re-pair"** state and surfaces a re-provision prompt (re-scan QR / reopen provisioning link). It must NOT silently retry the dead token in a tight loop.

---

## 6. WS connect + reconnect/re-auth state machine (`relay_client.js`)

States: `idle → connecting → ready → reconnecting → (back to connecting)` and a terminal `reauth_required`.

```
connect():
  open WS to {relayBaseUrl}/v1/client/{daemonId}/ws?client_id={stableClientId}
  on open      → state=connecting (await `ready`)
  on `ready`   → state=ready; record epoch; flush outbound queue; reconcile via replayed-id dedupe
  on message   → dedupe by envelope.id → dispatch to store; reply `ack`(delivery_state:"acked") for data kinds
  on `ping`    → send `pong`
  on `error`   → if matches a pending outbound (ref_id) mark that message failed; else surface non-fatal banner
  on close(code):
     code === 4003                → state=reauth_required (STOP; prompt re-provision)
     code === 1000 (normal)       → if intentional, stay closed; else reconnect
     otherwise (4001,4000,1006,1011,...) → state=reconnecting; backoff reconnect with SAME session
```

- **Backoff:** exponential with jitter, capped (e.g. 1s → 30s), reset on a successful `ready`. Mirrors the spirit of `ws.js`'s reconnect but with proper backoff (the existing local `ws.js` uses a flat 2s retry — we improve on it for a remote/flaky network).
- **Stable `client_id`:** generate once per device (persist in `localStorage`) so reconnects and replay target the same client slot.
- **Epoch:** record `epoch` from `ready`; if a later `ready` shows a higher epoch after a reconnect, that's a normal owner re-attach — no special action beyond continuing (we are a client, we don't own). Do not treat epoch change alone as re-auth.
- **Outbound queue:** while not `ready`, queue user_message/ask_reply; flush on `ready`. Each queued item retains its envelope `id` so a reconnect+replay won't duplicate (relay idempotency + our dedupe both protect).

**Open question O1 (engineer decision, low-risk):** Browsers can't set `Authorization` on the `WebSocket` constructor. Options for WS auth: (a) **cookie mode** (HttpOnly `torque_session` on relay origin) — cleanest, works for both WS + POST, recommend as primary; (b) token via query param (`?token=`) — simple but logs/leaks risk; (c) `Sec-WebSocket-Protocol` subprotocol carrying the token. **Recommendation: cookie mode primary; Bearer/query only for `local-dev-unauthenticated` testing where no token is needed at all.** Confirm relay accepts cookie on the WS upgrade (it does: `authenticateClientSession` reads `cookieValue(headers,"torque_session")`).

---

## 7. Rendering (`render_remote.js`)

- **Agent list pane:** one entry per `conversationsByAgent` key, sorted by `lastActivityTs`, showing agent name, last message preview, unread/pending-ask badge. Tapping switches active conversation.
- **Conversation pane:** message bubbles (user right / agent left), bodies via `markdown.js`, timestamps, per-outbound delivery state (pending/✓ delivered/✓✓ acked/✗ failed).
- **Ask cards (CONSUME-ONLY, no client-side recipient filtering):** the UI renders **exactly** the `ask`/`ask_reply` envelopes the connector delivers on the user↔agent lane and answers them — it does **not** decide which asks are "for the user." User-destination is resolved server-side (TORQUE:534 resolver + connector egress gating; see §0.5). `message_type==="ask"` (esp. `blocking`) renders a distinct card with an inline reply affordance; submitting sends an `ask_reply` (`reply_to_id` = ask id, same `agent_id`/`thread_id`). On ack/relay confirmation, the card collapses to a normal answered message. **The frontend never inspects `recipient_kind`/owner chain to filter asks** — that logic lives in the connector, consistent with the local DM-mirror behavior (recipient==user helper, S4 / :530 / :553).
- **Composer:** text input + send → `user_message` to the active agent (`agent_id` from active conversation). Disabled w/ banner when not `ready`.
- **Connection banner:** connecting / reconnecting (with backoff countdown optional) / **session expired — re-pair** (4003).
- **Rerender stability:** every rerender wraps in `surface.js` capture/restore so composer draft text, caret, and scroll position survive inbound-message rerenders. Follow the shared-memory caching-innerHTML gotcha (`bc9e1d0f9f66`): if we cache `innerHTML` for diff-stability, apply dynamic state at the cached element's own `.style`/`.dataset`, never by mutating descendants after the snapshot.

---

## 8. Switchable agents without a roster (V1)

- The agent list is **built from traffic**: the first `agent_message`/`ask` from an `agent_id` creates its conversation entry (using `agent_name` from the payload). This satisfies "multiple switchable USER↔AGENT conversations" because in practice agents initiate (asks, status messages) — the user answers/continues.
- **Out of V1:** discovering/initiating a conversation with an agent that has never messaged (no roster channel exists; adding one is board-state-adjacent and explicitly out of scope). Documented as a known V1 limitation.

---

## 9. Reuse vs purpose-built (assessed against `static/js/*`)

| Asset | Decision | Why |
|---|---|---|
| `markdown.js` | **Reuse (copy verbatim)** | Standalone IIFE, zero deps. |
| `render.js` `_captureSurfaceState`/`_restoreSurfaceState` + scroll-anchor | **Reuse (copy, trim board-isms)** | Already Node-tested; portable; needed for composer/scroll stability. |
| `chat.js` render logic | **Reference, purpose-built** | Tightly coupled to global `state.agent_peer_threads`, `renderChatPanel()`, local panel DOM. Reimplement compact renderer against our own store; borrow bubble/markdown structure. |
| `ws.js` | **Reference, purpose-built** | Hard-coupled to local daemon delta protocol + full board state + global `state` mutation. Our relay client is a different protocol (relay envelopes, not daemon deltas). Borrow only the reconnect *pattern* (and improve backoff). |
| `terminal.js` | **Skip** | PTY/xterm output; not conversation rendering. |

Net: a purpose-built minimal bundle, reusing two genuinely standalone helpers (`markdown.js`, surface capture/restore). Copying (not importing) keeps the EE bundle independent of community `static/js/` load order and avoids coupling the remote app to the operator app.

---

## 10. Local test setup

- Run standalone relay: `cd ee/relay && TORQUE_RELAY_DB=/tmp/relay.db npm run dev` (127.0.0.1:8787, `local-dev-unauthenticated`).
- Run a daemon connector outbound to that relay (or, for pure-UI tests, drive the relay's `POST /v1/messages/{daemonId}` to inject `agent_message`/`ask` and assert the UI renders + that `user_message`/`ask_reply` arrive).
- Serve `ee/frontend/remote/` with any static server; point config at the relay; in `local-dev-unauthenticated` no token needed.
- To exercise **4003 re-auth**: run relay in **redis mode** and revoke/owner-change, or unit-test the close-handler directly (see §12).

---

## 11. History-on-connect (FLAGGED product consequence) + optional follow-up

**V1 baseline (recommended, no backend change):** the remote UI shows replayed pending messages + live messages from connect-time forward. Acked history is not shown. This is the direct consequence of the locked "no board-state sync" contract and the connector not implementing `snapshot`. The UI should make this honest with a subtle "Showing new messages" affordance rather than implying full history.

**Optional, contract-compatible follow-up (OUT of V1 baseline; flag for engineer/human):** implement `snapshot_request`(client→daemon) / `snapshot`(daemon→client) in `connector.py` to send a bounded recent-conversation snapshot on connect. This is a connector change (EE Python), needs its own review, and is a **scope addition** — recommend deferring to P6.1 unless the user wants history in V1.

---

## 12. Tests (Node frontend regression + relay integration)

Follow the existing Node `vm`-sandbox harness in `tests/` (e.g. `tests/frontend_render_surface_focus.test.js`): load a module into a stubbed `document`/`globalThis` context and assert behavior. New tests (mirror naming `tests/frontend_remote_*.test.js`):

1. `frontend_remote_envelope.test.js` — envelope build/validate: `v:1`, ISO `created_at`, object payload, correct kinds for `user_message`/`ask_reply`; parse + accessor correctness.
2. `frontend_remote_dedupe.test.js` — duplicate envelope ids (at-least-once + replay overlap) are dropped exactly once; ordering preserved.
3. `frontend_remote_state_machine.test.js` — close-code handling: **4003 ⇒ reauth_required (no reconnect)**; 4001/4000/1006/1011 ⇒ reconnecting w/ backoff using same session; backoff caps + resets on `ready`; outbound queue flushes on `ready`.
4. `frontend_remote_store.test.js` — inbound `agent_message`/`ask`/`ask_reply` upsert into per-agent conversations; pendingAsks set/cleared; outbound delivery-state transitions on `ack`(ack_id) / `error`(ref_id); tail cap enforced.
5. `frontend_remote_render.test.js` — agent-list + conversation + ask-card render; composer draft + scroll survive an inbound-message rerender (surface capture/restore); innerHTML-cache gotcha guard (dynamic state on container, descendants not mutated).
6. **Relay integration (optional, Node):** drive a real ephemeral standalone relay (`createStandaloneRelayServer({port:0})`) — POST an `agent_message`, connect the client, assert receipt + dedupe; send `user_message`, assert `202`/ack. Keep within `ee/relay`'s test runner if that's cleaner than the repo `tests/` harness.

Keep the full `make test` suite green; add the new frontend tests to whatever runner discovers `tests/frontend_*.test.js`.

---

## 13. Ordered implementation steps

1. **Scaffold** `ee/frontend/remote/` (index.html, css, empty module files); update `ee/frontend/README.md`. Confirm community-packaging guard still passes (`make assert-community-package`).
2. **`config.js`** — bootstrap parse/validate + endpoint derivation; tests.
3. **Copy `markdown.js` + `surface.js`** (trim board-isms); port the relevant Node tests.
4. **`envelope.js`** — build/validate/parse; `frontend_remote_envelope.test.js`.
5. **`relay_client.js`** — WS connect, `ready`/`ack`/`error`/ping-pong, HTTP POST, dedupe, outbound queue, reconnect/re-auth state machine; `frontend_remote_dedupe.test.js` + `frontend_remote_state_machine.test.js`.
6. **`store.js`** — conversation model, upsert, delivery-state, pendingAsks, tail cap; `frontend_remote_store.test.js`.
7. **`render_remote.js` + `app.js`** — agent list, conversation, ask cards, composer, connection banner; surface stability; `frontend_remote_render.test.js`.
8. **Local end-to-end** against standalone relay (§10); optional relay-integration test (§12.6).
9. **Mandatory review** of the implementation (per task), then user-gated go-live (hosting/exposure) handled separately.

---

## 14. Risks / open questions

- **O1 (WS auth header):** cookie-mode primary; confirm provisioning sets HttpOnly `torque_session` on the relay origin. (See §6.)
- **O2 (history-on-connect):** confirm V1 ships replay-only history (recommended) vs adding the connector snapshot follow-up. (See §11.)
- **O3 (cross-origin):** if the remote UI is served from a different origin than the relay, cookie mode needs `SameSite=None; Secure` + CORS on the relay HTTP POST; relay CORS posture for client POST should be confirmed at go-live (not a V1 local-test blocker since same-origin/loopback).
- The two pinned relay auth/coordination warnings (`de786e96a469`) are about relay-side code we are NOT touching — but they reinforce: **the client must never treat a cached session/epoch as authority**; always honor the relay's 4003 close as the source of truth for "re-auth now."
- **O4 (ask-stream dependency — see §0.5):** the remote-ask surface is **blocked on TORQUE:534 + a connector-egress gating fix** (gate `ask`/`ask_reply` on user-destination). The non-ask conversation surface (`user_message`/`agent_message`) is **not** blocked and can be built/tested independently. Sequencing options for the implementer: (i) build the full UI now but keep ask cards behind a flag / accept that local tests inject only user-destined asks, then enable once :534 + connector gating land; or (ii) split P6 into a conversation slice (now) + an ask slice (after :534). Recommend (i) — the UI code is identical either way; only the upstream stream cleanliness changes.

---

## Approval recommendation

**Engineer-approvable.** The plan is bounded and low-risk: it is a **pure client of the already-shipped relay contract**, lives entirely under the already-excluded `ee/frontend/`, introduces **no backend/relay/connector/API changes, no migrations, no schema changes, and no remote exposure** (local build+test only; go-live is a separately-gated step). It stays strictly within the LOCKED V1 product shape.

Items the engineer should explicitly sign off on (and escalate to human only if product judgment is wanted):
- **O2 / §11 — history-on-connect:** ship replay-only history in V1 (recommended) and defer the optional connector `snapshot` follow-up. If the user wants full history in V1, that becomes a scope addition (connector change + its own review) and needs **human approval**.
- **§0.1 close-code resolution** (4003⇒re-auth, others⇒reconnect) and **§6 O1** (cookie-mode WS auth) — confirm these match intended UX.

**Cross-team dependency to route (not a frontend code change):**
- **§0.5 / O4 — connector ask-stream gap:** the remote-ask surface depends on **TORQUE:534** + a **connector-egress gating fix** so `ask`/`ask_reply` are forwarded to the user lane **only when user-destined** (today they are forwarded unconditionally — `connector.py:633-645`). **Route this to Courier (relay/connector owner).** The frontend will consume the resolved stream and must not filter recipients itself. This does not block building/testing the `user_message`/`agent_message` conversation surface.

The eventual implementation carries **mandatory review**.
