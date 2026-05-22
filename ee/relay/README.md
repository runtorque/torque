# Torque EE Relay Spike

This is an isolated enterprise spike for **Channels / remote Torque**. It does not modify or start the local Python daemon. The goal is to prove the relay can share core TypeScript contracts while swapping target adapters:

- Storage port: `RelayStore`
  - Standalone: `SqliteRelayStore` using Node's `node:sqlite`
  - Cloudflare: `D1RelayStore` against a `D1Database` binding
- Coordination/rendezvous port: `RelayCoordinator`
  - Standalone: `StandaloneRegistryCoordinator` in-process single-owner registry
  - Cloudflare: `DaemonRendezvousDurableObject` scaffold using Durable Object WebSocket hibernation hooks
- Entrypoint seam:
  - Standalone: Node `http` + `ws` server
  - Cloudflare: Worker `fetch` handler + DO routing

## Local spike commands

```sh
cd ee/relay
npm install
npm test
npm run dev
```

`npm run dev` starts the standalone relay on `127.0.0.1:8787` by default. Set `PORT` and `TORQUE_RELAY_DB` to override.

## Cloudflare scaffold

`wrangler.toml` declares a D1 binding and a Durable Object namespace. Replace `REPLACE_WITH_D1_DATABASE_ID` after creating a D1 database, then use Wrangler with the user's Cloudflare account. This task intentionally does **not** validate a live deploy.
