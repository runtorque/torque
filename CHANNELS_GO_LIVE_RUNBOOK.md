# Channels GO-LIVE Runbook + Readiness Plan

**Scope:** SCOPE-FIRST / PLAN ONLY. No remote exposure, no Cloudflare deploy, no secrets in repo. This artifact documents the deploy mechanics, the readiness-verification suite that MUST pass before exposure, the security posture, and the recommended de-risking sequence. **Actual exposure remains the FINAL explicit user gate after verified readiness.**

**Grounded against (read, current branch):**
- `ee/relay/wrangler.toml`, `ee/relay/src/adapters/cloudflare/{worker,durableObjectCoordinator,d1Store}.ts`
- `ee/relay/src/core/auth.ts` (ES256 signed-attach, client sessions, `selectStandaloneAuthMode`)
- `ee/relay/src/adapters/standalone/server.ts` (`/v1/pair`, `/v1/admin/pairing-tokens`, `/v1/admin/client-sessions`, `/v1/messages`)
- `ee/relay/migrations/0001_relay.sql`, `0002_relay_coordination.sql`; `ee/relay/src/core/sql.ts`
- `ee/python/torque_ee_connector/{auth,connector,protocol}.py`; `torque/cloud_hooks.py`; `torque/config.py`
- `ee/frontend/remote/*` (remote browser client); `scripts/assert_community_package_excludes_ee.py`
- Prior artifacts: `~/.torque/attachments/TORQUE:569/channels-phase-4-authenticated-owner-plan.md`, `:544` design.

---

## ⚠️ TOP-LINE BLOCKER (read first)

**The Cloudflare Worker has NO provisioning surface.** `src/adapters/cloudflare/worker.ts` only routes:
- `GET /health` (public; `?migrate=1` runs migrations)
- `GET /v1/{daemon,client}/:id/ws` (WS upgrade → Durable Object; auth enforced in DO)
- `POST /v1/messages/:daemon_id` (client-session-authenticated HTTP enqueue)

It does **NOT** expose `/v1/pair`, `/v1/admin/pairing-tokens`, or `/v1/admin/client-sessions`. **Those endpoints exist only in the standalone Node server** (`src/adapters/standalone/server.ts`). The `D1RelayStore` has all the underlying methods (`createPairingToken`, `consumePairingToken`, `createDaemonCredential`, `createClientSession`), but the Worker never calls them.

**Consequence:** On a deployed Cloudflare relay there is currently **no HTTP way** to mint a pairing token, pair a daemon, or mint a client session. The admin bootstrap token (`TORQUE_RELAY_BOOTSTRAP_TOKEN`) the task references gates the **standalone** admin endpoints; it is **not consumed by the Worker at all**.

This forces a decision (see **§1.5 Decision: CF provisioning path**). The rest of the runbook is written so either path is executable, but this decision must be made before deploy.

A second, smaller note: the connector **does not auto-pair**. It reads a pre-existing `credential_id` + private key and signs the attach. Pairing (keypair gen → token → `POST /v1/pair` → store credential) is a separate, currently-manual provisioning workflow with no shipped CLI. See **§1.4**.

---

## ═══ 1. DEPLOY MECHANICS (ee/relay → Cloudflare) ═══

### 1.1 What the USER sets up Cloudflare-side (one-time, manual)

These are account/console actions only the account owner can do. Enumerated exactly:

1. **Cloudflare account** with Workers enabled (free tier supports Workers + D1 + Durable Objects on the SQLite-backed DO class, which this uses via `new_sqlite_classes`).
2. **D1** enabled on the account (no separate toggle beyond Workers; created via wrangler in §1.3).
3. **Durable Objects** enabled. The DO class `DaemonRendezvousDurableObject` uses `new_sqlite_classes` (SQLite-backed DO), which is available on the Workers free plan; confirm the account is not on a plan that restricts DO.
4. **A known-good Cloudflare API token** for wrangler (see **§3.1** for exact
   setup). Prefer a Cloudflare **Account API token** created under the account
   that owns the Worker + D1 database; a My Profile user token also works if it
   includes the User read permissions called out below. The user places it in
   their **gitignored** `.env` as `CLOUDFLARE_API_TOKEN` (+ `CLOUDFLARE_ACCOUNT_ID`). **Never commit. Never put in any community artifact.**
5. **(Only if a custom domain is wanted)** a zone in the account + DNS control. The default `*.workers.dev` subdomain needs no zone and is the recommended go-live target for V1 (smaller token scope, simpler).
6. **Decision input** for the provisioning path (§1.5) and the `owner_user_id` string to use (the single V1 owner identity; e.g. `owner-<you>`).

### 1.2 What WE configure (in-repo / commands, no secrets)

- `ee/relay/wrangler.toml` — currently a spike scaffold:
  ```toml
  name = "torque-ee-relay-spike"
  main = "src/adapters/cloudflare/worker.ts"
  compatibility_date = "2026-05-22"
  compatibility_flags = ["nodejs_compat"]
  [[d1_databases]]
  binding = "RELAY_DB"
  database_name = "torque-ee-relay-spike"
  database_id = "REPLACE_WITH_D1_DATABASE_ID"
  migrations_dir = "migrations"
  [[durable_objects.bindings]]
  name = "RENDEZVOUS"
  class_name = "DaemonRendezvousDurableObject"
  [[migrations]]
  tag = "v1"
  new_sqlite_classes = ["DaemonRendezvousDurableObject"]
  ```
  **Config edits (no secrets):**
  - Rename `name` and `database_name` off `-spike` to the go-live name (e.g. `torque-ee-relay`). Cosmetic but the `-spike` name signals non-production.
  - Replace `database_id = "REPLACE_WITH_D1_DATABASE_ID"` with the real id from §1.3.
  - `compatibility_date` is fine; bump to deploy date if desired.
  - `migrations_dir = "migrations"` points at the D1 SQL migration files (separate concept from the `[[migrations]]` DO class migration, which is correct as-is).

### 1.3 D1 provisioning + migrations (wrangler)

From `ee/relay/` with `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` exported from the user's shell (the user runs these — wrangler login/token is a user action; suggest `! <cmd>` so output lands in-session):

```sh
cd ee/relay && npm install
# 1. Create the D1 database; copy the returned database_id into wrangler.toml.
npx wrangler d1 create torque-ee-relay
# 2. Apply schema migrations (0001 + 0002) to the REMOTE D1.
npx wrangler d1 migrations apply torque-ee-relay --remote
```

Notes:
- Migrations are `0001_relay.sql` (full schema v4: `relay_meta`, `relay_instances`, `relay_messages`, `relay_pairing_tokens`, `relay_daemon_credentials`, `relay_auth_nonces`, `relay_client_sessions` + indexes) and `0002_relay_coordination.sql` (adds the three `relay_instances` coordination columns; the runtime adapters also self-heal these via `PRAGMA table_info` so re-running 0002 on a fresh 0001 schema is harmless/idempotent).
- The Worker also has a `GET /health?migrate=1` hatch that runs `RELAY_SCHEMA_STATEMENTS`. This is convenient but **public and unauthenticated** — see **§3.5**. Prefer `wrangler d1 migrations apply` as the canonical path; treat the `?migrate=1` hatch as a flagged item to remove/gate before public exposure.
- Verify schema landed: `npx wrangler d1 execute torque-ee-relay --remote --command "SELECT name FROM sqlite_master WHERE type='table';"`

### 1.4 Daemon credential + ES256 pairing (currently manual)

The connector authenticates with **ES256/P-256 signed attach**: it signs a canonical `GET /v1/daemon/:daemon_id/ws` string (protocol marker, method, path, daemon_id, credential_id, timestamp, nonce, empty-body-hash) with a local private key. The relay stores only the **public key JWK** + `credential_id` + `owner_user_id`. Private key never leaves the local profile.

The credential must exist in the relay **before** the daemon can attach. Provisioning a credential = one `relay_daemon_credentials` row + the owner stamped on the `relay_instances` row. Two ways to create it:

**(A) via standalone `/v1/pair`** (works locally / in the dress-rehearsal — see §1.5/Decking):
1. Generate a P-256 keypair locally (no shipped tool; one-liner): `openssl ecparam -genkey -name prime256v1 -noout -out daemon_key.pem` then derive the public JWK (Python `cryptography` or `jwk` tooling), or generate directly in Python with `cryptography` and export both PEM (private) and JWK (public).
2. Mint a pairing token (bootstrap-authenticated): `POST /v1/admin/pairing-tokens` with `Authorization: Bearer <TORQUE_RELAY_BOOTSTRAP_TOKEN>` and body `{owner_user_id, daemon_id, label}` → returns `pairing_token`.
3. Pair: `POST /v1/pair` with `{pairing_token, daemon_id, public_key_jwk, credential_id?}` → returns `credential_id` + stamps `relay_instances.owner_user_id`.
4. Store locally (see §1.6).

**(B) via direct D1 seeding** (the only path on the CF Worker today, since `/v1/pair` is not exposed there): insert the `relay_daemon_credentials` row + `relay_instances` owner row directly with `wrangler d1 execute`. The `public_key_jwk_json` is the JWK string; `alg='ES256'`; `owner_user_id` is the V1 owner. No token-hashing needed for credentials (the public key is not a secret). Example shape:
```sh
npx wrangler d1 execute torque-ee-relay --remote --command \
 "INSERT INTO relay_daemon_credentials (credential_id,daemon_id,owner_user_id,public_key_jwk_json,alg,created_at,last_used_at,revoked_at,metadata_json) VALUES ('cred-...','<daemon_id>','<owner>','{...jwk...}','ES256','<iso>','','','{}');"
npx wrangler d1 execute torque-ee-relay --remote --command \
 "INSERT INTO relay_instances (id,owner_user_id,label,created_at,last_seen_at,fencing_epoch,active_credential_id,coordination_updated_at,metadata_json) VALUES ('<daemon_id>','<owner>','<daemon_id>','<iso>','<iso>',0,'cred-...','<iso>','{}') ON CONFLICT(id) DO UPDATE SET owner_user_id=excluded.owner_user_id, active_credential_id=excluded.active_credential_id;"
```

### 1.5 DECISION: CF provisioning path (blocking choice for the user/architect)

Because the Worker lacks `/v1/pair` + `/v1/admin/*`, choose one:

- **Path A — D1 direct-seed (no code change; recommended for first go-live).** Seed `relay_daemon_credentials` + `relay_instances` (daemon) and `relay_client_sessions` (client) directly via `wrangler d1 execute`. Client-session tokens are stored **hashed** (SHA-256 hex of the raw token; see `hashSecret` in `auth.ts`) — compute the hash locally and insert the hash; hand the raw token to the device out-of-band. Pros: zero code change, stays in-scope for this plan. Cons: manual SQL, hash-by-hand for sessions, no convenience UI.
- **Path B — port the admin/pair endpoints into the Worker (code change; OUT OF SCOPE here).** Add `/v1/pair`, `/v1/admin/pairing-tokens`, `/v1/admin/client-sessions` to `worker.ts`, gated by a `TORQUE_RELAY_BOOTSTRAP_TOKEN` CF secret (mirroring `verifyBootstrap`). Pros: parity with standalone, repeatable. Cons: new authenticated admin surface on the public Worker (must be `required`-mode-safe and rate-limited), and it is **net-new implementation** that needs its own plan + review + tests. **Flag for architect:** if Path B is wanted, it is a separate implementation task, not part of this go-live runbook.

**Recommendation:** Path A for V1 go-live (smallest blast radius, no new public admin surface). Revisit Path B only if repeated re-provisioning becomes painful.

### 1.6 Local daemon CONNECTOR config to dial + pair the deployed relay

The connector is loaded by the open-core daemon only when explicitly enabled (`torque.cloud_hooks.start_cloud_connector`, gated by `CLOUD_CONNECTOR_ENABLED`). Enable + configure:

- **Enable:** `TORQUE_EE_CONNECTOR_ENABLED=1` (or `TORQUE_CLOUD_CONNECTOR_ENABLED=1`); ensure `ee/python` is importable (`PYTHONPATH=.../ee/python`). `cryptography` must be installed (EE-only dep) — without it signed attach disables cleanly and a remote URL is rejected.
- **Relay URL:** `TORQUE_EE_RELAY_URL=https://<worker>.workers.dev` (the connector normalizes to `wss://.../v1/daemon/<daemon_id>/ws`). **Non-loopback ⇒ `wss://` REQUIRED and signed credential REQUIRED** (`config_from_context` rejects otherwise).
- **Daemon id:** `TORQUE_EE_DAEMON_ID=<daemon_id>` (must match the paired credential/instance).
- **Credential + key (the local secret):** put in `~/.torque/profiles/<profile>/ee_connector.json` at mode **0600** (the loader rejects group/world-accessible files):
  ```json
  {"credential_id": "cred-...", "private_key_path": "/abs/path/daemon_key.pem"}
  ```
  (`private_key_path` file must also be 0600; or inline `private_key_pem`.) Env equivalents: `TORQUE_EE_DAEMON_CREDENTIAL_ID`, `TORQUE_EE_DAEMON_PRIVATE_KEY_PEM`. When `credential_id` + key are present the connector switches `auth_mode` to `signed-attach-v1` and sends the `Torque-Daemon-Signature v1 ...` header on the WS upgrade.
- **Required-mode auth posture:** the connector refuses a non-loopback URL without a signed credential, and refuses non-`wss://`. This is the daemon-side half of the fail-closed posture.

### 1.7 Worker entrypoint + DO bindings recap

- Entrypoint: `worker.ts` default export `{ fetch }`; re-exports `DaemonRendezvousDurableObject`. Deploy with `npx wrangler deploy` (= `npm run cf:deploy`).
- Bindings consumed at runtime: `RELAY_DB` (D1) and `RENDEZVOUS` (DO namespace). Both declared in `wrangler.toml`. The DO authenticates daemon/client sockets in `authenticateRoute` **before** `acceptWebSocket`/`attachSocket`, and re-checks credential/session validity on hibernation rehydrate (closing revoked sockets with `4003`).
- The Worker `/health` reports `auth_mode: "required"` unconditionally — there is no local-dev-unauth path on CF (good; see §3.4).

### 1.8 Runtime SECRETS on Cloudflare (enumerated)

Set as CF secrets via `npx wrangler secret put <NAME>` — **never** in repo, `wrangler.toml`, or any community artifact:

| Secret | Needed? | Purpose |
|---|---|---|
| `TORQUE_RELAY_BOOTSTRAP_TOKEN` | **Only under Path B** (porting admin endpoints). Not read by the Worker today. | Gates `/v1/admin/pairing-tokens` + `/v1/admin/client-sessions` minting. |
| (none else) | — | The current Worker reads no other secrets; D1/DO are bindings, not secrets. Daemon ES256 private key and client-session raw tokens are **local/device** secrets, never CF secrets. |

**Local secrets (not CF):** ES256 private key PEM (`0600`), connector config file (`0600`); the user's gitignored `.env` holding the **wrangler `CLOUDFLARE_API_TOKEN`** (deploy-time, not relay-runtime).

### 1.9 Remote frontend hosting (note, Panelsmith-owned)

The Worker does **not** serve the `ee/frontend/remote/` bundle. The remote browser UI is a separate static site (e.g. Cloudflare Pages or any static host) configured with `relayBaseUrl=https://<worker>.workers.dev` + `daemonId`. Cookie-mode (`HttpOnly torque_session`) is the only browser-viable WS-upgrade auth (the WebSocket constructor can't set headers). Hosting + the cookie path is Panelsmith's surface (see §2b).

---

## ═══ 2. READINESS VERIFICATION SUITE (runs BEFORE exposure) ═══

Marked **[MINE]** (this work stream) vs **[PANELSMITH'S]**. Each item lists what proves it.

### (a) [MINE — LOAD-BEARING] Fully-authenticated client attach with an ES256-paired daemon in REQUIRED mode — END TO END

This is **the** load-bearing proof. Today the hijack-closed auth path is **only unit-tested** with `FakeD1Database` + a fake DO namespace (`test/workerAuth.test.ts`, `test/durableObjectCoordinator.test.ts`); **no test runs the full required-mode chain over a real WS upgrade.** Prove the whole path:

1. Daemon has a real ES256 credential provisioned (via `/v1/pair` locally, or D1-seed against CF).
2. Daemon attaches: signed `Torque-Daemon-Signature v1` header accepted in `required` mode → `ready` with epoch ≥ 1. **Verify a wrong/expired/replayed signature is rejected with no epoch bump and no owner replacement** (the TORQUE:569 hijack-closure property).
3. Client session attaches (cookie or bearer): `authenticateClientSession` passes (active session, owner matches daemon owner) → client `ready`.
4. End-to-end flow: user→agent `user_message` reaches the daemon's `remote_user_agent_message` ingress and is acked; agent→user `agent_message` + `ask` egress reach the client; an `ask` is answered from the client (`user_message` + `reply_to_id`) and the answer reaches the agent.

Run this **locally first** (dress-rehearsal, §De-risking) against a `required`-mode standalone relay, then **again** post-deploy against CF (§2d).

### (b) [PANELSMITH'S — flag for her] Real-browser ambient-cookie WS upgrade

The `HttpOnly torque_session` cookie carrying through the **client WS upgrade in a real browser** (not the Node fake-socket harness). `auth.ts` reads the cookie via `cookieValue(... "torque_session")`; the remote bundle relies on `credentials:'include'`. Needs: a real browser, a relay origin that sets the cookie, and a same-origin (or correctly `SameSite`/CORS-configured) WS upgrade. **Owner:** Panelsmith. **Flag:** this is the one readiness item that cannot be proven from the daemon/relay side and is not in the Node suite.

### (c) [check TORQUE:587 trigger] Double-bubble real-daemon check

Confirm the TORQUE:587 "double-bubble" condition (duplicate render/echo of a user message under the real daemon) does not reproduce against a real daemon + relay round-trip. The snapshot/live de-dupe is by message id (`dm:<id>:<kind>`), and the connector gates ask egress to user-destined only — verify no message appears twice (snapshot + live, or local echo + relayed). **Trigger check:** review whether :587 is open/relevant before exposure; if open, it gates go-live.

### (d) [MINE] Deployed smoke test against the deployed CF relay (post-deploy, pre-public-exposure)

After `wrangler deploy`, before any public URL is shared:
1. `GET /health` → `{auth_mode:"required", storage:"d1", coordination:"durable-object"}`.
2. Negative auth: unauthenticated `POST /v1/messages/:id` → 401 and **no D1 row** written (mirrors `workerAuth.test.ts` but against the live Worker). Unauthenticated/`?token=`-only client WS upgrade → rejected.
3. Positive path: the §2a end-to-end flow, now through the deployed Worker + DO + D1 (real hibernation possible). Verify DO hibernation rehydrate keeps the owner and closes a revoked credential/session with `4003`.
4. Confirm the daemon connector (local) dials `wss://<worker>.workers.dev`, signs attach, and stays connected through a reconnect.

**Exit criteria for the whole suite:** (a) green locally AND on CF (d); (b) confirmed by Panelsmith; (c) :587 not reproducing; negative-auth assertions all fail-closed. Only then present the exposure gate.

---

## ═══ 3. SECURITY ═══

### 3.1 Known-good Cloudflare API token for Wrangler + remote D1

The previously documented stripped token (**Account → Workers Scripts → Edit** +
**Account → D1 → Edit** + **Account → Account Settings → Read** + the custom
domain Zone permissions) was **not sufficient** for the first relay
auto-deploy: `npx wrangler d1 migrations apply RELAY_DB --remote` failed on
`/accounts/.../d1/database/.../query` with Cloudflare code **7403** ("account is
not valid or is not authorized"). Do not repeat that stripped scope.

Use this known-good baseline until a narrower token has been re-smoke-tested
against both `wrangler d1 migrations apply --remote` and `wrangler deploy`:

1. Create a token from Cloudflare's **Edit Cloudflare Workers** template (the
   same template Cloudflare documents for Wrangler/GitHub Actions) and keep its
   defaults:
   - **Account → Workers Scripts → Edit/Write**.
   - **Account → Workers KV Storage → Edit/Write**.
   - **Account → Workers R2 Storage → Edit/Write**.
   - **Account → Workers Tail → Read**.
   - **Account → Account Settings → Read**.
   - **Zone → Workers Routes → Edit/Write** (scope the zone resource to the
     target zone when using a route/custom domain).
   - For a My Profile/user token, also keep **User → User Details → Read** and
     **User → Memberships → Read**. Account-owned tokens do not have User
     resources, but must be created under the target account.
2. Add **Account → D1 → Edit/Write**. This is the permission that covers D1
   database creation, `wrangler d1 execute`, and remote migration SQL hitting
   the `/d1/database/{database_id}/query` API.
3. Under **Account resources**, include the exact account whose id is exported as
   `CLOUDFLARE_ACCOUNT_ID` and that owns `TORQUE_RELAY_D1_DATABASE_ID`. A token
   with D1 permission on the wrong account, or a D1 database id from a different
   account, can surface as code 7403 during the remote D1 query step.
4. Add **only if** using a custom domain (the committed
   `relay.runtorque.com` config does): **Zone → DNS → Edit/Write** and
   **Zone → Zone → Read**, scoped to the single target zone. The Workers Routes
   permission comes from the template above.

Do **not** grant account-admin, all-zone, or unrelated product scopes as the
default. If debugging forces a broader token, treat it as temporary: first
confirm the account resource and D1 database id, then narrow back to the baseline
above and prove it with:

```sh
npx wrangler d1 execute RELAY_DB --remote --command "SELECT 1;"
npx wrangler d1 migrations apply RELAY_DB --remote
```

Store the token in the gitignored `.env` only.

### 3.2 Secret handling — nothing in repo / nothing in community

- CF runtime secrets (if any, i.e. Path B's bootstrap token) → `wrangler secret put` only.
- Local secrets (ES256 private key, connector config, client-session raw tokens) → `0600` files / device-local; never logged (the connector/auth code already avoids logging keys, tokens, signatures, headers).
- Wrangler `CLOUDFLARE_API_TOKEN` → user's gitignored `.env`.
- **Community-exclusion confirmed:** `ee/` is enterprise-only. `scripts/assert_community_package_excludes_ee.py` builds the community install artifact and fails if any file with an `ee` path part leaks, and pins the allowed top-level set to `{static, torque, torque.py, torque_desktop.py, webview.html, .torque_source_repo_root}`. **Action: run `make assert-community-package` as part of readiness** to confirm the guard still excludes `ee/` (relay, connector, frontend, secrets) from the open build. No secret value should ever appear in any artifact uploaded to the community/shared surfaces.

### 3.3 Required-mode auth posture AT exposure (TORQUE:569 GATE 1)

**Non-loopback ⇒ AUTH MANDATORY, fail-closed.** Enforced at two layers, confirm both at exposure:
- **Relay:** the CF Worker hard-codes `auth_mode: "required"`; the DO authenticates every daemon/client socket before attach; `selectStandaloneAuthMode` makes `local-dev-unauthenticated` **structurally impossible** for non-loopback bind hosts (throws `non_loopback_requires_auth`). The deployed relay therefore cannot run in the relaxed mode. **Verify `/health` reports `auth_mode:"required"`.**
- **Connector:** refuses a non-loopback relay URL without a signed credential and refuses non-`wss://`.

GATE 1 = confirm the deployed relay is `required` (never `local-dev-unauthenticated`) before exposure. This holds by construction on CF; the verification is the `/health` check in §2d.

### 3.4 Daemon pairing flow + credential rotation / revocation

- **Pairing:** one-time, hashed pairing token (single-use, short expiry) → daemon submits public JWK → relay creates `relay_daemon_credentials` + stamps `relay_instances.owner_user_id`. (Or D1-seed under Path A.)
- **Rotation:** multiple active credentials per daemon are allowed; a new credential attaches with a new epoch and replaces the old socket only if the owner matches (`daemon_owner_mismatch` otherwise). Rotate by seeding/pairing a new credential, repointing the connector, then revoking the old.
- **Revocation:** set `revoked_at` on the credential (`revokeDaemonCredential`) or session (`revokeClientSession`). Revoked credentials/sessions cannot attach; **hibernated DO sockets are closed on rehydrate** (`attachmentStillAuthorized` → `4003 authenticated_session_revoked`). Client sessions revoke independently of daemon credentials.
- **Replay protection:** `relay_auth_nonces` enforces nonce uniqueness within the skew window (default 300s); timestamp skew is rejected. Captured attach headers cannot be replayed.

### 3.5 Additional security observations (flag, low/medium)

- **`GET /health?migrate=1` is public + unauthenticated** and runs schema statements. It is idempotent (`CREATE TABLE IF NOT EXISTS`), so the risk is a cheap repeated-DDL/DoS nuisance, not data exposure. **Recommend removing or gating the `?migrate=1` hatch before public exposure** (run migrations via wrangler instead). This is a small code change — flag for the engineer/architect, not part of this plan's execution.
- **Worker→DO internal route** (`/internal/route-to-daemon/...`) is reached only via the DO namespace binding, not publicly routable through the Worker's path matching — acceptable, but note it as defense-in-depth surface if Path B adds more routes.
- **`name = "...-spike"`** in `wrangler.toml` signals non-production; rename before go-live so the deployed Worker isn't labelled a spike.

---

## ═══ DE-RISKING / RECOMMENDED SEQUENCING ═══

**Yes — a LOCAL full-stack dress-rehearsal of the SAME stack should precede the CF deploy.** Rationale: §2a is the load-bearing, never-end-to-end-tested path; the standalone relay supports an explicit `required` auth mode AND has the `/v1/pair` + `/v1/admin/*` endpoints the CF Worker lacks, so the local run exercises the identical auth/pairing/session logic (shared `core/auth.ts`, shared schema) **with** a working provisioning surface, before introducing CF/D1/DO + the provisioning gap.

Recommended order:

1. **Local dress-rehearsal (no exposure, loopback `required` mode):**
   - Start standalone relay in **required** mode on loopback: `TORQUE_RELAY_AUTH_MODE=required TORQUE_RELAY_BOOTSTRAP_TOKEN=<dev> TORQUE_RELAY_DB=/tmp/relay.db npm run dev` (loopback + `required` is permitted by `selectStandaloneAuthMode`).
   - Provision via `/v1/admin/pairing-tokens` → `/v1/pair` (daemon ES256) and `/v1/admin/client-sessions` (client).
   - Run the daemon with the EE connector in `signed-attach-v1` mode against `ws://127.0.0.1:8787` *(note: loopback allows ws; remote forces wss)* and run the remote bundle (`python3 -m http.server`).
   - Execute readiness **§2a** and the negative-auth assertions locally. This is the "turnkey local-run / option a" dress-rehearsal of the same stack.
2. **`make test` + `make assert-community-package`** green (full suite + community-exclusion guard).
3. **CF deploy** (§1.3 migrations → §1.5 provisioning decision → `wrangler deploy`).
4. **Deployed readiness §2d** (smoke + the §2a flow against CF) + Panelsmith **§2b** + **§2c** :587 check.
5. **USER EXPOSURE GATE** — explicit user approval to share the public URL. Final gate; not granted by this plan or by the engineer/architect.

---

## Approval path

**Human approval is REQUIRED.** This is the highest-stakes step (remote exposure of the orchestration system) and carries security/operational decisions beyond bounded implementation:
- the CF provisioning-path decision (§1.5: D1-seed vs porting admin endpoints),
- the `?migrate=1` hatch handling (§3.5),
- the required-mode/exposure posture and the final exposure gate.

This artifact is **plan-only**; no code changed. Per the task flow: **Courier (engineer) reviews → relays to the architect → architect surfaces the concrete plan + the user's required CF inputs** (§1.1) and the §1.5 decision. **Actual exposure remains the final explicit user gate after the §2 readiness suite passes.**

No `make deploy/stop/restart`; no implementation derive until approval.
