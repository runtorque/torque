# Channels / Remote Torque — design artifact + dual-target relay spike

Task: `TORQUE:544`
Date: 2026-05-22
Status: design + de-risking spike ready for human approval gate

## 1. Executive summary

**Recommendation:** ship Channels as an **enterprise-only remote control-plane relay** while keeping the local Torque daemon as the source of truth and execution boundary. The daemon connects **outbound** to the relay over a versioned WebSocket/JSON protocol. Remote clients and channel adapters (Slack first) talk to the relay; the relay only authenticates, rendezvous, persists small transport metadata, and forwards envelopes to the local daemon.

This preserves the core architectural invariant: agents, terminals, worktrees, SQLite state, dispatch, and merges remain local. Cloud code is a thin control-plane/relay against a shared contract.

The included spike under `ee/relay/` proves the key portability claim:

- one TypeScript relay contract and core port surface;
- `RelayStore` implemented for **standalone SQLite** and **Cloudflare D1**;
- `RelayCoordinator` implemented for **standalone in-process single-owner registry** and scaffolded for **Cloudflare Durable Objects with WebSocket hibernation attachment restore**;
- standalone Node entrypoint is runnable locally without a Cloudflare account;
- Cloudflare Worker/Durable Object/D1 scaffold and Wrangler config are present but not live-deployed.

## 2. Confirmed constraints and non-goals

### Constraints anchored by the task

- Local Python daemon remains source of truth and runs all agents/worktrees.
- Cloud is a relay/control plane only; agents do **not** move to cloud.
- Local daemon initiates outbound connection; no inbound LAN tunnel required.
- Relay is a new JS/TS codebase, ports-and-adapters, runnable on Cloudflare and standalone Node.
- Three seams must be abstracted:
  1. `RelayStore`: D1 vs SQLite/libSQL/Postgres-style standalone storage.
  2. coordination/rendezvous: Durable Object vs standalone registry/Redis.
  3. entrypoint: Worker `fetch` vs Node `http`+`ws`.
- Enterprise (`ee/`) contains the relay and remote frontend components and must be excluded from community packaging.
- Open-source core gains clean extension seams only.

### Non-goals for this task

- No Python daemon integration implemented here.
- No Torque core packaging behavior changed here.
- No full Slack app implementation.
- No Cloudflare deploy, because it requires the user's Cloudflare account and D1 database ID.
- No migration of Torque's local SQLite data model.

## 3. Current Torque architecture relevant to Channels

Ground truth from code + pinned shared memory `2abfb98eed60`:

### Local messaging model

- `agent_peer_messages` is the single durable table for all user↔agent, architect↔engineer, architect↔architect, and ask-mirror messages.
  - Schema: `torque/db_schema.py` and migration guard in `torque/db.py::_ensure_agent_peer_messages_schema`.
  - Direct/user rows are identified by `sender_kind='user' OR recipient_kind='user' OR message_type!='message' OR blocking!=0`.
- Deterministic user↔agent V1 thread IDs are built by `torque.db.canonical_user_agent_thread_id(agent_id, user_id='user')`.
- User→agent sends are already command-shaped as `cmd='user_agent_message'` in `torque/server.py::_handle_user_agent_message_command`.
- Agent→user replies are already MCP-shaped via `torque_message_user`, `engineer_message_user`, and `architect_message_user`; the common persistence helper is `torque/mcp_tools_shared.py::save_agent_user_direct_message_from_mcp`.
- Blocking asks are mirrored into direct messages without changing ask semantics via `torque/direct_message_mirrors.py`.

### Live UI model

- `MatrixState` exposes `direct_messages_by_agent` and `agent_peer_threads` in snapshots (`torque/state.py`).
- WebSocket deltas already support `direct_message_upsert`, `direct_message_read`, `peer_message_upsert`, and `agent_peer_thread_upsert` (`static/js/ws.js`).
- The below-terminal direct-message panel renders from `state.direct_messages_by_agent[agentId]` (`static/js/terminal.js`).
- The read-only peer Chat panel renders `state.agent_peer_threads` (`static/js/chat.js`).

### Existing seams the connector can reuse

- The local daemon already has a command dispatcher (`handle_command` in `torque/server.py`) used by UI WS, `/api/cmd`, and MCP.
- Remote user→agent can map to the existing `user_agent_message` command instead of inventing a second message table.
- Agent→user replies can be observed from the existing direct-message persistence/delta path and published to the relay.
- Snapshot/delta state can be filtered and forwarded rather than reconstructing Torque state in cloud.

## 4. Target architecture

```text
Slack / remote browser / mobile
          │
          ▼
┌──────────────────────────────────────────────────────────────┐
│ Enterprise relay (TS, ports-and-adapters)                     │
│                                                              │
│  entrypoint: Worker fetch or Node http+ws                    │
│  auth/session: user/device/channel auth                      │
│  channels: Slack, future Teams/Discord/etc.                  │
│  storage: RelayStore (D1 or SQLite/libSQL/Postgres)          │
│  rendezvous: Durable Object or standalone registry/Redis     │
└───────────────┬──────────────────────────────────────────────┘
                │ outbound WebSocket/JSON from local daemon
                ▼
┌──────────────────────────────────────────────────────────────┐
│ Local Torque daemon (Python, source of truth)                 │
│                                                              │
│  torque/server.py + MatrixState + local SQLite               │
│  iTerm2/terminal adapters                                    │
│  agents/worktrees/dispatch/merges                            │
└──────────────────────────────────────────────────────────────┘
```

Cloud never becomes the execution host. It stores relay metadata, connection/rendezvous state, channel installation data, and optionally a bounded remote audit/log. It does **not** authoritatively store board tasks, agent state, or worktree state.

## 5. Relay protocol: local daemon ↔ relay

### Transport

- WebSocket, daemon initiated outbound.
- JSON envelopes, language-agnostic, versioned.
- One logical daemon connection per local Torque profile/installation.
- Remote clients/channel adapters connect to relay; the relay forwards to the active daemon connection for that daemon ID.

### Route sketch

Standalone Node and Cloudflare use equivalent routes:

- `GET /health`
- `GET /v1/daemon/:daemon_id/ws` — local daemon outbound connection.
- `GET /v1/client/:daemon_id/ws?client_id=...` — browser/mobile/remote client connection.
- `POST /v1/messages/:daemon_id` — optional HTTP enqueue path for channel adapters or tests.

### Envelope

```json
{
  "v": 1,
  "id": "msg-...",
  "trace_id": "trace-...",
  "daemon_id": "desktop-profile-uuid",
  "source": { "kind": "remote-client", "id": "browser-1", "user_id": "user" },
  "target": { "kind": "daemon", "id": "desktop-profile-uuid" },
  "kind": "user_message",
  "created_at": "2026-05-22T00:00:00.000Z",
  "payload": {
    "agent_id": "f197a2a6",
    "thread_id": "user-agent:user:f197a2a6",
    "message": "What is the status?",
    "reply_to_id": ""
  }
}
```

### Required V1 kinds

- `hello`: connection intro/capabilities from daemon or client.
- `ready`: relay acknowledges attach, returns connection epoch and online state.
- `ping` / `pong`: liveness and clock skew hints.
- `snapshot_request`: remote asks local daemon for scoped state.
- `snapshot`: local daemon returns scoped state (board/messages/agent summaries, not raw DB).
- `user_message`: remote user/channel asks daemon to deliver to an agent.
- `agent_message`: local direct-message row sent back to remote client/channel.
- `ask` / `ask_reply`: direct-message ask mirrors surfaced remotely.
- `ack`: transport-level receipt for replay/idempotency.
- `error`: typed failure.
- `channel_event`: normalized Slack/etc. event before local delivery.

### Idempotency and ordering

- Relay envelope `id` is the idempotency key at the relay storage layer.
- Local direct-message idempotency remains the existing Torque message ID logic; the daemon maps remote envelope IDs to existing `idempotency_key` where applicable.
- Relay ordering is per `daemon_id` + `created_at` + `id`, not global.
- Durable Object / standalone registry guarantees a single active daemon connection per `daemon_id`; message delivery to the daemon is therefore linearized by one owner.
- Durable local semantics still live in the daemon's SQLite write path.

### Local-daemon connector behavior (future implementation)

The enterprise connector inside the local Python process should:

1. Read config: relay URL, daemon ID, pairing/session credentials, selected profile/group scope.
2. Open outbound WebSocket to `/v1/daemon/:daemon_id/ws`.
3. Send `hello` with capabilities and protocol version.
4. On `user_message`, call the existing command path equivalent of `cmd='user_agent_message'`.
5. On local `direct_message_upsert` / `direct_message_read`, send `agent_message`/read state envelopes to relay.
6. On state changes requested by remote UI, send bounded `snapshot` + deltas using existing snapshot/delta projection helpers.
7. Never expose arbitrary `handle_command` to cloud; only allow a narrow, audited remote command allowlist.

## 6. Ports and adapters

### 6.1 `RelayStore`

Interface implemented in spike: `ee/relay/src/core/ports.ts`.

Responsibilities:

- migrate relay schema;
- upsert local daemon/relay instance rows;
- persist relay envelopes with direction;
- list bounded message history;
- mark delivered/acked.

Adapters in spike:

- Standalone: `ee/relay/src/adapters/standalone/sqliteStore.ts`
  - Uses Node's `node:sqlite`.
  - Runs locally without external services.
- Cloudflare: `ee/relay/src/adapters/cloudflare/d1Store.ts`
  - Uses D1 `prepare(...).bind(...).run()/all()/first()` against the same SQL contract.

Schema in spike:

- `relay_instances`: one row per paired local daemon/profile.
- `relay_messages`: transport envelope audit/replay rows.

This intentionally stores only relay metadata/envelopes, not the authoritative Torque board or local agent DB.

### 6.2 Coordination / rendezvous

Interface implemented in spike: `RelayCoordinator` in `ee/relay/src/core/ports.ts`.

Responsibilities:

- attach one active daemon socket per `daemon_id`;
- attach N remote clients/channel sessions to that daemon;
- route remote→daemon and daemon→clients;
- detach sockets;
- expose a bounded snapshot of connection state.

Adapters in spike:

- Standalone: `ee/relay/src/adapters/standalone/registryCoordinator.ts`
  - In-process maps.
  - Enforces a single active daemon owner by closing/replacing old daemon sockets and incrementing an epoch.
  - Good for local dev and single-process deployments.
- Cloudflare: `ee/relay/src/adapters/cloudflare/durableObjectCoordinator.ts`
  - Each `daemon_id` routes to one Durable Object instance via `idFromName(daemon_id)`.
  - Uses `acceptWebSocket`, `serializeAttachment`, and `deserializeAttachment` to scaffold WebSocket hibernation-safe session restoration.

Future standalone multi-instance equivalent:

- Redis lease + pub/sub adapter, with a fencing token/epoch equivalent to Durable Object single-owner state.
- The API must keep epoch/fencing in the port so Redis and DO semantics remain comparable.

### 6.3 Entrypoint

Adapters in spike:

- Standalone: `ee/relay/src/adapters/standalone/server.ts`
  - Node `http` + `ws`.
  - Provides `/health`, WebSocket routes, and HTTP enqueue route.
- Cloudflare: `ee/relay/src/adapters/cloudflare/worker.ts`
  - Worker `fetch` entrypoint.
  - Routes WebSocket upgrades to the Durable Object namespace.
  - `/health?migrate=1` can run the D1 schema migration during scaffold/dev.

## 7. Rendezvous and consistency model

The hard seam is the Durable Object's single-owner consistency. V1 should model this explicitly:

- `daemon_id` is the rendezvous key.
- Exactly one active daemon connection is valid at a time.
- Attach returns an `epoch`.
- Reconnecting daemon increments epoch and fences stale sockets.
- Remote clients can stay attached while daemon reconnects; they receive offline/online state in future deltas.
- Relay can buffer bounded remote→daemon envelopes while daemon is offline, but local delivery is not acknowledged until the daemon accepts/writes them.
- A relay `delivered_at` means transport delivery to active daemon/client, not local persistence in Torque; local persistence should be confirmed by a daemon `ack` envelope.

Cloudflare semantics:

- Durable Object instance is the single coordinator for a daemon key.
- WebSocket hibernation keeps client sockets connected while the DO sleeps; attachments restore enough session metadata when it wakes.
- D1 stores durable relay rows; DO storage can later hold small per-daemon ephemeral/lease metadata if needed.

Standalone semantics:

- Single-process registry matches DO semantics for local dev.
- Multi-process standalone must not use independent in-process registries. Add Redis adapter with:
  - `SET lease daemon:{id} NX/PX` or equivalent;
  - monotonically increasing epoch/fencing token;
  - pub/sub channels per daemon;
  - atomic compare-and-set on owner replacement.

## 8. Channels as adapters layered on the relay

Channels should be adapter plugins on the relay side, not local-daemon plugins.

Slack V1 flow:

1. Slack event arrives at relay adapter via Events API HTTP endpoint or Socket Mode.
2. Slack adapter verifies Slack auth/signature/token and normalizes event to `channel_event` or `user_message` envelope.
3. Relay coordinator routes to active local daemon.
4. Local daemon maps to existing `user_agent_message` delivery path.
5. Agent replies with `torque_message_user` / architect/engineer variants.
6. Local connector emits `agent_message` envelope.
7. Slack adapter maps to `chat.postMessage` or thread reply.

Slack implementation note: Slack supports Events API over HTTP or Socket Mode. Socket Mode is attractive for local/dev and firewall scenarios, but the relay already has a public endpoint, so production Cloudflare can use HTTP Events API and Web API writes. Slack docs describe both Events API delivery choices and Socket Mode's WebSocket shape; the adapter should hide that choice behind a `ChannelAdapter` port.

Suggested channel adapter port:

```ts
interface ChannelAdapter {
  platform: 'slack' | string;
  verifyInbound(request: Request): Promise<ChannelEvent>;
  normalize(event: ChannelEvent): RelayEnvelope;
  deliver(envelope: RelayEnvelope): Promise<ChannelDeliveryResult>;
}
```

## 9. Enterprise boundary and packaging

### Proposed `ee/` monorepo structure

```text
ee/
  relay/                         # new TS relay project (spike exists now)
    src/core/                    # shared protocol + ports
    src/adapters/standalone/     # Node http/ws, SQLite, registry
    src/adapters/cloudflare/     # Worker, D1, Durable Object
    src/channels/slack/          # future Slack adapter
    migrations/                  # D1/SQLite-compatible SQL
    docs/                        # design docs
  python/                        # future enterprise Python extension package
    torque_ee_connector/         # outbound relay client loaded by local daemon
  frontend/                      # future remote UI panels/components
```

### Community/core extension seams

Core should stay open and ignorant of enterprise code. Add only generic hooks:

1. **Outbound cloud connector hook**
   - Core file likely: `torque/server.py` startup/shutdown and `torque/config.py` config flags.
   - Shape: optional import/registration of connector callbacks if an EE package is installed and enabled.
   - Community default: no-op.

2. **Remote message ingress helper**
   - Core file likely: `torque/server.py` or a new `torque/remote_ingress.py`.
   - Shape: narrow function that accepts a sanitized remote direct-message command and calls existing `_handle_user_agent_message_command` logic.
   - Do not expose arbitrary `handle_command` remotely.

3. **Direct-message outbound observer**
   - Core file likely: `MatrixState.save_direct_message`, `append_direct_message_to_caches`, or server-level delta observer.
   - Shape: optional callback/event sink for rows already being emitted as direct-message deltas.

4. **EE frontend injection hook**
   - Core files likely: `webview.html`, `torque/server.py`, `static/js/panel_manager.js`.
   - Shape: a manifest injection point that is empty in community packaging and can serve enterprise JS/CSS when present.
   - Preserve current no-build frontend and script-order constraints.

### Packaging rules

Current `Makefile` copies only `torque/`, `static/`, `webview.html`, and entrypoints. That already excludes `ee/` unless future targets add it. Keep it that way for community.

Future packaging:

- Community: unchanged `make install`, `make deploy`, Tauri packaging excludes `ee/`.
- Enterprise: separate `make ee-build` / `make ee-deploy-relay` / desktop bundle step that includes signed EE assets and connector package.
- CI/release should assert no `ee/` files are included in community artifacts.
- Frontend injection should fail closed: if EE assets/manifest absent, no remote UI is visible.

## 10. Open forks for approval

### Fork A — Auth model

#### Option A1: single-user remote-first

Shape:

- One owner user per local daemon/profile.
- Pairing flow creates daemon credential + remote user session.
- Remote Slack/browser identity maps to the existing synthetic Torque `user` for V1.
- Data model keeps `owner_user_id` and `user_id` fields so multi-user can be added later.

Pros:

- Fastest path to safe value.
- Aligns with current Torque messaging model: one synthetic `user`, no multi-user/group direct-message semantics.
- Lower security/product surface: pairing, revocation, device sessions.
- Avoids premature RBAC while remote execution boundaries are still being proven.

Cons:

- Team/multi-tenant Slack installations need explicit later work.
- Audit UX can say which remote account sent a message, but local core still sees V1 `user` unless extended.

#### Option A2: multi-tenant from day one

Shape:

- Multiple cloud users/orgs/roles per relay account.
- Local daemon receives distinct user principals.
- Remote command policy and board visibility are RBAC-scoped from V1.

Pros:

- Better long-term SaaS/team shape.
- Cleaner audit identity if done correctly.

Cons:

- Conflicts with current V1 direct-message assumption of one synthetic `user`.
- Forces product/security decisions before the relay and local connector are proven.
- Larger migration surface across `agent_peer_messages`, frontend, and CLI read models.

**Recommendation: Option A1 single-user remote-first**, with multi-tenant-compatible fields (`owner_user_id`, envelope `source.user_id`, channel installation tables) but no V1 RBAC/multi-user behavior. Require human approval before moving beyond A1.

### Fork B — EE granularity

#### Option B1: all Channels enterprise

Shape:

- Relay, local connector, channel adapters, and remote frontend live under `ee/`.
- Community core exposes no-op extension seams only.

Pros:

- Clean packaging/privacy boundary.
- Avoids leaking half-useful remote code into community with no hosted relay.
- Matches task constraint that relay + remote frontend are enterprise hidden.
- Easier security review: remote control-plane code is in one layer.

Cons:

- Community cannot self-host a basic relay unless a later policy changes.
- Open-source contribution to Channels internals is limited.

#### Option B2: basic-open + advanced-enterprise

Shape:

- Open-source local connector/protocol/basic standalone relay.
- Enterprise contains Cloudflare deployment, Slack adapters, hosted relay, advanced auth/admin.

Pros:

- More transparent protocol.
- Potential community self-hosting story.

Cons:

- Harder to prevent accidental community packaging of remote surfaces.
- More support burden and more attack surface in OSS.
- The task explicitly anchors relay and remote frontend in EE.

**Recommendation: Option B1 all Channels enterprise for V1**, while keeping the *interfaces/protocol concepts* documented enough that the OSS core hooks remain clean and testable. Reconsider B2 only after the security model settles.

## 11. Compatibility with future `:545` vector-DB/cloud foundation

Keep `:545` compatible by treating this relay as the shared EE/cloud foundation, not a one-off Slack bridge:

- `ee/relay/src/core` owns protocol, auth principal, and storage abstractions that future vector/search services can reuse.
- Add future ports beside `RelayStore`, e.g. `VectorStore` or `RemoteIndexStore`, rather than embedding vector concerns into message routing.
- Keep tenant/owner IDs in relay records now even if V1 is single-user.
- Keep local daemon as source of truth; vector DB should index replicated/summarized artifacts, not own Torque state.
- Channel adapter auth/session tables should be separate from future vector/index tables but share user/daemon identity primitives.

## 12. Spike inventory

Files added under isolated `ee/relay/`:

- `package.json`, `package-lock.json`, `tsconfig.json`, `.gitignore`
- `wrangler.toml`
- `migrations/0001_relay.sql`
- `src/core/protocol.ts`
- `src/core/ports.ts`
- `src/core/sql.ts`
- `src/adapters/standalone/sqliteStore.ts`
- `src/adapters/standalone/registryCoordinator.ts`
- `src/adapters/standalone/server.ts`
- `src/adapters/cloudflare/d1Store.ts`
- `src/adapters/cloudflare/durableObjectCoordinator.ts`
- `src/adapters/cloudflare/worker.ts`
- `test/sqliteStore.test.ts`
- `test/d1Store.test.ts`
- `test/registryCoordinator.test.ts`
- `README.md`

### Local verification run

From `ee/relay`:

```sh
npm test
```

Result:

- TypeScript build passed.
- Node tests passed: 3/3.
- Covered standalone SQLite store, D1 store via a local fake D1 binding, and standalone registry single-owner routing/replacement.

### Cloudflare validation boundary

The Cloudflare adapter is scaffolded and type-checked, but not deployed. Live validation requires:

- user Cloudflare account;
- `wrangler login` or API token;
- real D1 database ID replacing `REPLACE_WITH_D1_DATABASE_ID`;
- `wrangler d1 migrations apply` / deploy workflow decision.

## 13. Implementation plan after approval

Human approval is required before implementation because this introduces a remote-control architecture, auth/security decisions, EE/community packaging boundaries, and eventually externally visible Slack behavior.

### Phase 0 — approval and option lock

Files: this design artifact, task approval thread.

- Approve or change auth recommendation (single-user remote-first).
- Approve or change EE granularity recommendation (all Channels enterprise V1).
- Confirm Cloudflare as first hosted target and standalone Node as dev/self-host target.

### Phase 1 — harden relay spike into EE package

Files:

- `ee/relay/src/core/*`
- `ee/relay/src/adapters/standalone/*`
- `ee/relay/src/adapters/cloudflare/*`
- `ee/relay/test/*`
- `ee/relay/wrangler.toml`

Work:

- Add explicit auth/session interfaces without implementing full SaaS RBAC.
- Add bounded offline queue behavior and ack/replay tests.
- Add structured error envelopes and protocol compatibility tests.
- Add standalone server integration test with real `ws` client connections.
- Add Cloudflare/miniflare tests if practical without an account.

Tests:

- `npm test` in `ee/relay`.
- New protocol contract tests for envelope validation and replay ordering.

### Phase 2 — add open-core no-op extension seams

Files likely:

- `torque/server.py`
- new `torque/remote_ingress.py` or `torque/cloud_hooks.py`
- `torque/state.py`
- `torque/config.py`
- `webview.html`
- `static/js/panel_manager.js` or a small new injection script
- tests under `tests/`

Work:

- Add no-op connector lifecycle hooks at daemon startup/shutdown.
- Add a narrow remote direct-message ingress helper that reuses current `user_agent_message` logic.
- Add direct-message outbound observer hook after local persistence/delta emission.
- Add frontend manifest injection point that is empty unless EE is installed.
- Ensure community packaging remains unchanged and excludes `ee/`.

Tests:

- Python tests proving hooks are no-op by default.
- Tests proving remote ingress validates allowlisted shape and calls the same direct-message path.
- Packaging test proving `ee/` is not copied into community install/deploy artifacts.
- Frontend regression test for injection no-op preserving script order and render stability.

### Phase 3 — enterprise local outbound connector

Files likely:

- `ee/python/torque_ee_connector/*`
- minimal open-core hook registration glue
- tests in `ee/python/tests/*` plus core hook tests

Work:

- Implement outbound WebSocket client from local daemon to relay.
- Implement `hello`, `ready`, `ping/pong`, reconnect/backoff, and epoch handling.
- Map relay `user_message` to the remote ingress helper.
- Publish direct-message rows/deltas back to relay.
- Add config for relay URL, pairing/session token, enabled groups/profile.

Tests:

- Local fake relay integration tests.
- Reconnect/offline queue tests.
- Security tests that arbitrary `handle_command` cannot be invoked remotely.

### Phase 4 — productionize relay auth and storage

Files:

- `ee/relay/src/core/auth*`
- `ee/relay/src/adapters/*`
- `ee/relay/migrations/*`

Work:

- Implement pairing flow, daemon credentials, session/device tokens, revocation.
- Add channel installation tables.
- Add audit tables for remote principal/channel provenance.
- Add Redis coordination adapter only if standalone multi-process is needed.
- Add operational metrics/logging.

Tests:

- Auth unit tests.
- Revocation and replay tests.
- D1 migration tests.
- Standalone SQLite compatibility tests.

### Phase 5 — remote frontend components

Files:

- `ee/frontend/*`
- open-core frontend injection hook tests
- possibly `ee/relay/src/adapters/standalone/server.ts` for static serving

Work:

- Build remote conversation UI using relay snapshots/deltas.
- Start with direct user↔agent threads; defer broad board editing.
- Preserve current frontend live-update stability patterns.

Tests:

- Node frontend regression tests for remote panel state preservation.
- Snapshot/delta contract tests.

### Phase 6 — Slack adapter

Files:

- `ee/relay/src/channels/slack/*`
- `ee/relay/migrations/*`
- tests under `ee/relay/test/slack*`

Work:

- Implement Slack event verification and install config.
- Normalize Slack mentions/DM/channel messages into relay envelopes.
- Deliver agent replies via Slack Web API.
- Map Slack threads to Torque direct-message threads.
- Add rate-limit/retry handling and idempotency.

Tests:

- Signed Slack request verification tests.
- Event normalization tests.
- `chat.postMessage` delivery mock tests.
- Duplicate/retry idempotency tests.

## 14. Key risks and mitigations

- **Remote command overreach:** only expose allowlisted message/snapshot operations; never raw `handle_command`.
- **Identity ambiguity:** V1 maps to synthetic `user`, but envelope/source/audit fields retain remote identity for logs and future migration.
- **DO vs standalone consistency drift:** keep epoch/fencing in `RelayCoordinator` contract; add Redis adapter before multi-process standalone.
- **Cloud stores too much Torque state:** persist only relay/audit/channel metadata; daemon remains source of truth.
- **Frontend script-order fragility:** EE injection must be explicit and tested; no wholesale subtree replacement in live panels.
- **Packaging leak:** add community artifact tests before wiring any EE hook into packaging.

## 15. References consulted

- Cloudflare D1 Worker API (`D1Database.prepare().bind().run()/all()`): https://developers.cloudflare.com/d1/worker-api/d1-database/
- Cloudflare Durable Objects WebSocket hibernation (`acceptWebSocket`, `serializeAttachment`, `deserializeAttachment`): https://developers.cloudflare.com/durable-objects/best-practices/websockets/
- Cloudflare Durable Object migrations and `new_sqlite_classes`: https://developers.cloudflare.com/durable-objects/reference/durable-objects-migrations/
- Wrangler configuration for D1 and Durable Object bindings/migrations: https://developers.cloudflare.com/workers/wrangler/configuration/
- Slack Events API delivery model: https://docs.slack.dev/apis/events-api/
- Slack Socket Mode overview: https://api.slack.com/apis/connections/socket
- Slack `chat.postMessage`: https://api.slack.com/methods/chat.postMessage
