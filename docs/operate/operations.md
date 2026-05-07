# Operations

This page covers the operator-facing side of Torque: how it runs, how to update it, where to look when it breaks, and which runtime modes exist.

## Runtime Modes

### Toolbelt mode

This is the default mode. Torque runs from iTerm2's Scripts menu and registers its UI in the Toolbelt.

### Dual mode

Run Torque normally in the Toolbelt, then open the same UI in a browser:

```bash
make open
```

Both clients talk to the same daemon and stay in sync.

### Standalone-only mode

Run Torque without Toolbelt registration:

```bash
make standalone
make open
```

This still controls iTerm2, but the UI is only served in the browser.

### Native desktop shell

Run Torque in a real native window through `pywebview`:

```bash
make desktop-deps
torque desktop
```

By default, the desktop shell starts its own standalone Torque server with
desktop-specific runtime values so it does **not** accidentally attach to the
Toolbelt daemon:

- profile: `desktop`
- port: `18933`
- data dir: `~/.torque/profiles/desktop`

If you already have a matching standalone server and want to reuse it, attach
explicitly:

```bash
torque desktop --attach --profile desktop --port 18933
# or
make desktop-attach
```

Attach mode is intentionally conservative:

- it only reuses an existing **standalone** Torque server
- it refuses to target the iTerm2-hosted Toolbelt runtime
- it refuses to attach when the profile or data dir does not match
- it never shuts down an external server that it did not spawn

Spawn mode owns the child server lifecycle and stops that child when the native
window closes. Attach mode only owns the window.

## Install and Update

Initial install:

```bash
make deps
make install
make cli
```

Update after pulling new changes:

```bash
make deploy
```

Then restart from **Scripts -> torque**.

Useful maintenance targets:

- `make stop`
- `make check`
- `make autolaunch`
- `make uninstall`
- `make restart`
- `make desktop-deps`
- `make desktop`
- `make desktop-attach`

## Logs

Torque writes its daemon log to:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log
```

Tail it from the CLI:

```bash
torque logs
torque logs -f
```

## Notifications

Torque supports macOS notifications for agent activity. Notification behavior is configured per group.

Available toggles include:

- master notifications switch
- notify on finish
- notify on error
- notify on attention-needed

These settings live in [Group Settings](group-settings.md).

## Runtime State

Persistent state lives in SQLite:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db
```

Read-only CLI commands can fall back to SQLite directly, which is why commands like `torque task list` and `torque ai context` can still work even when the daemon is stopped.

## Common Problems

### Port already in use

```bash
make stop
```

### Toolbelt panel missing

Make sure:

1. iTerm2's Python API is enabled
2. Torque was started from **Scripts -> torque**
3. The Toolbelt entry is enabled from the gear menu

### CLI cannot talk to the daemon

Check:

- the daemon is running
- the port matches `TORQUE_PORT`
- the log file contains no startup error

### Browser view does not update

The browser and Toolbelt both use the same WebSocket stream. If one view is stale, refresh it and check the daemon log before assuming the frontend is the problem.

### Native desktop shell will not launch

Check these first:

1. Run `make check` and confirm `pywebview` is installed.
2. Remember that `pywebview` must be installed in the **runtime launching the desktop shell**. On a standard Torque install, that means the iTerm2-managed Python environment used by `torque desktop`.
3. If you passed `--python` or `TORQUE_DESKTOP_PYTHON`, install `pywebview` into that interpreter too.
4. If attach mode fails, confirm the target server is standalone and that its `TORQUE_PROFILE`, `TORQUE_PORT`, and `TORQUE_DATA_DIR` match the desktop shell values exactly.

## Environment Variables

Common runtime variables:

| Variable | Description |
|---|---|
| `TORQUE_PORT` | HTTP/WebSocket port |
| `TORQUE_STANDALONE` | Skip Toolbelt registration when set |
| `TORQUE_DEFAULT_CMD` | Default boot command |
| `TORQUE_BIND_ALL` | Bind to `0.0.0.0` instead of localhost |
| `TORQUE_DESKTOP_MODE` | Desktop shell lifecycle mode: `spawn` or `attach` |
| `TORQUE_DESKTOP_PROFILE` | Desktop shell profile override (defaults to `desktop`) |
| `TORQUE_DESKTOP_PORT` | Desktop shell port override (defaults to `18933`) |
| `TORQUE_DESKTOP_DATA_DIR` | Desktop shell data-dir override |
| `TORQUE_DESKTOP_PYTHON` | Python runtime used by `torque desktop` |

## Platform Expectations

- **Validated now:** macOS + iTerm2, including the native `pywebview` shell.
- **Browser-only smoke:** the served UI path can also be exercised in a browser, but that does not prove native-window behavior.
- **Not yet a supported operator target:** Linux and Windows. `pywebview` is cross-platform, but Torque still depends on iTerm2 integration and has not been validated there yet.

## Related Docs

- [Getting Started](../foundations/getting-started.md)
- [Group Settings](group-settings.md)
- [CLI Reference](../reference/cli.md)
- [Architecture](../architecture.md)
