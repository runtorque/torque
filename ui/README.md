# Torque React UI

This directory contains the replacement browser renderer described in
[`docs/plans/react-web-ui-rfc.md`](../docs/plans/react-web-ui-rfc.md). React is
the primary browser and Tauri renderer at `/`. `/ui-next/` remains a compatible
React alias for migration bookmarks, while `/legacy/` keeps the classic client
available during the fixed burn-in window.

## Development

Use the Node version in `.nvmrc` and install exactly the committed dependency
graph:

```bash
npm --prefix ui ci
make ui-check
```

Run an isolated daemon, then use `make ui-dev` for the browser client or
`make ui-tauri-dev` for Vite plus the Tauri host. `TORQUE_PORT` and
`TORQUE_UI_DAEMON_ORIGIN` select the daemon used by Vite. `TORQUE_UI_URL`
selects the renderer origin loaded by the Tauri shell.

The source boundaries are deliberate:

- `src/protocol/` owns daemon wire formats and projection reducers;
- `src/app/` owns Redux composition and the application shell;
- `src/host/` owns browser/Tauri capabilities;
- `src/extensions/` owns the versioned optional-feature registry;
- `src/design/` owns global tokens, reset styles, and accessible primitives;
- `src/features/` owns Board, Agents, terminal, Planning, and Control Center
  product slices; cross-feature state stays in the app projection.

The Board browser suite expects an isolated daemon and Vite proxy. Point
`TORQUE_UI_BASE_URL` at Vite and set `TORQUE_PLAYWRIGHT_CHANNEL=chrome` when
using an installed Chrome instead of Playwright's bundled Chromium:

```bash
TORQUE_UI_BASE_URL=http://127.0.0.1:5174 \
TORQUE_PLAYWRIGHT_CHANNEL=chrome npm --prefix ui run test:e2e
```

## Dependency updates

Dependencies are updated intentionally, one coherent group at a time. Use
`npm --prefix ui install <package>@<version>` (or the corresponding `--save-dev`
form), review both `package.json` and `package-lock.json`, then run
`make ui-check`. Review upstream release notes for runtime, build, security,
browser-support, and Node-version changes. Do not hand-edit resolved versions
or replace the lockfile with another package manager.

CI always uses `npm ci`; a lockfile mismatch is therefore a hard failure.

## Generated assets and packaging

`dist/`, coverage, Playwright output, and `node_modules/` are generated and
ignored. Do not commit them. `npm run build` typechecks, emits route-independent
hashed assets, and verifies `dist/index.html`, the Vite manifest, and its entry
asset. Production source maps are disabled.

`install-standalone` copies only `ui/dist/`. It must never copy source,
dependencies, tests, or enterprise paths. The community extension registry is
empty and the packaging guard inspects the resulting artifact. Release jobs
install the pinned Node version, run `npm ci`, build and verify the UI, and only
then build the Tauri bundle.

Private product builds compose their separately supplied registry with
`composeExtensionRegistries`. Registry version, ids, core-panel collisions,
titles, placement, loaders, and declared runtime capabilities are validated
before rendering. Community source never imports a private registry; the
package audit also scans built assets for private-path markers.

See [`docs/operate/react-ui-migration.md`](../docs/operate/react-ui-migration.md)
for browser/Tauri operation, burn-in diagnostics, and renderer-only rollback.
