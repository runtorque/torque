# Torque EE Relay

Enterprise-only Channels relay package for **remote Torque**. The local Python daemon remains the source of truth and execution host; this package provides the thin remote relay/control-plane that remote clients and future channel adapters use to reach an outbound-connected daemon.

Phase 1 hardens the original spike while staying isolated under `ee/relay/`:

- **Versioned wire protocol** (`src/core/protocol.ts`): V1 WebSocket/JSON envelopes, all local↔cloud message kinds, validation, `ack` and structured `error` envelopes.
- **Storage port** (`RelayStore` in `src/core/ports.ts`): shared schema/migration SQL in `src/core/sql.ts`, idempotent append by envelope id, delivery state, bounded pending-list/replay, and SQLite/D1 adapters.
- **Coordination port** (`RelayCoordinator` in `src/core/ports.ts`): single-owner daemon rendezvous with monotonic epoch/fencing, standalone registry adapter, and Durable Object adapter using hibernated WebSocket attachments.
- **Entrypoint seam**: standalone Node `http`+`ws` server plus Cloudflare Worker fetch/DO routing scaffold.
- **Runtime** (`src/core/runtime.ts`): shared offline queue, replay, delivery-attempt marking, and ack handling used by the standalone entrypoint and intended as the common behavior contract for Cloudflare follow-up hardening.

> Security boundary: Phase 1 intentionally does **not** implement auth. Standalone/relay attach remains unauthenticated for local/dev only. Auth, pairing, and owner-hijack protection are Phase 4 and required before any remote exposure.

## Public surface for later phases

Subsequent Channels slices should depend on this surface instead of duplicating relay logic:

- `RelayEnvelope`, `RelayMessageKind`, `RELAY_PROTOCOL_VERSION`, `RELAY_MESSAGE_KINDS`, `makeRelayEnvelope`, `makeAckEnvelope`, `makeErrorEnvelope`, `parseRelayEnvelope*` from `src/core/protocol.ts`.
- `RelayStore`, `RelayCoordinator`, `RelaySocket`, `StoredRelayMessage`, `RelayDeliveryResult`, `RelayBroadcastResult`, and rendezvous snapshot types from `src/core/ports.ts`.
- `RELAY_SCHEMA_STATEMENTS` / `migrations/0001_relay.sql` as the portable SQLite/D1 schema source.
- `RelayRuntime` for idempotent ingest, offline queue, bounded replay, ack, and epoch-fenced daemon delivery.
- Adapters:
  - `SqliteRelayStore` and `StandaloneRegistryCoordinator` for local standalone/dev.
  - `D1RelayStore`, `DaemonRendezvousDurableObject`, and Worker `fetch` for Cloudflare.

## Local commands

```sh
cd ee/relay
npm install
npm run lint
npm test
npm run dev
```

`npm run dev` starts the standalone relay on `127.0.0.1:8787` by default. Set `PORT` and `TORQUE_RELAY_DB` to override. Use `createStandaloneRelayServer({ port: 0 })` in tests to bind an ephemeral port.

## Cloudflare scaffold

`wrangler.toml` declares a D1 binding and a Durable Object namespace. Replace `REPLACE_WITH_D1_DATABASE_ID` after creating a D1 database, then use Wrangler with the user's Cloudflare account. Phase 1 type-checks and tests the adapter shape locally, but intentionally does **not** validate a live deploy.

Cloudflare Phase 1 limitation: the Worker/DO path is still a scaffold for account-gated deployment. It persists HTTP enqueue rows in D1 and routes active sockets through the Durable Object, but full Cloudflare replay/ack orchestration is not yet wired through `RelayRuntime`; that belongs with the later daemon connector/auth/productionization slices before exposure.

## Protocol routes

Standalone and Cloudflare expose equivalent routes:

- `GET /health`
- `GET /v1/daemon/:daemon_id/ws` — outbound local daemon WebSocket.
- `GET /v1/client/:daemon_id/ws?client_id=...` — remote browser/client WebSocket.
- `POST /v1/messages/:daemon_id` — HTTP enqueue path for tests and future channel adapters.

## Out of scope for this package slice

- Python daemon connector or any changes outside `ee/relay/`.
- Production auth/session/pairing.
- Redis/multi-process standalone coordination.
- Community packaging exclusion guards.
- Remote frontend and Slack adapter.

## Phase 4 auth boundary

Phase 4 turns the relay from a local unauthenticated spike into a fail-closed
owner-attach surface for any reachable deployment:

- daemon WebSocket attach uses `Torque-Daemon-Signature v1` over a canonical
  `GET /v1/daemon/:daemon_id/ws` request with ES256/P-256, timestamp, and nonce;
- the relay verifies the credential, owner, timestamp, and nonce **before** it
  calls `attachDaemon`, so rejected attach attempts cannot increment the epoch,
  replace the current owner, mutate `relay_instances`, or trigger replay;
- client WebSocket/HTTP message ingress uses separate owner sessions and V1
  forces envelope `source.user_id` to the synthetic Torque `user`;
- Cloudflare/Durable Object paths require auth; standalone loopback may use the
  explicit `local-dev-unauthenticated` mode, but non-loopback bind hosts force
  `auth_mode=required` at server construction and reject attempts to select the
  relaxed mode.

Minimal standalone provisioning helpers exist for tests/dev (`/v1/admin/pairing-tokens`,
`/v1/pair`, `/v1/admin/client-sessions`). They are not a go-live surface; remote
exposure still requires a separate user gate.
