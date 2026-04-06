# Operations

This page covers the operator-facing side of Loom: how it runs, how to update it, where to look when it breaks, and which runtime modes exist.

## Runtime Modes

### Toolbelt mode

This is the default mode. Loom runs from iTerm2's Scripts menu and registers its UI in the Toolbelt.

### Dual mode

Run Loom normally in the Toolbelt, then open the same UI in a browser:

```bash
make open
```

Both clients talk to the same daemon and stay in sync.

### Standalone-only mode

Run Loom without Toolbelt registration:

```bash
make standalone
make open
```

This still controls iTerm2, but the UI is only served in the browser.

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

Then restart from **Scripts -> loom**.

Useful maintenance targets:

- `make stop`
- `make check`
- `make autolaunch`
- `make uninstall`
- `make restart`

## Logs

Loom writes its daemon log to:

```text
~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log
```

Tail it from the CLI:

```bash
loom logs
loom logs -f
```

## Notifications

Loom supports macOS notifications for agent activity. Notification behavior is configured per group.

Available toggles include:

- master notifications switch
- notify on finish
- notify on error
- notify on attention-needed

These settings live in [Group Settings](group-settings.md).

## Runtime State

Persistent state lives in SQLite:

```text
~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db
```

Read-only CLI commands can fall back to SQLite directly, which is why commands like `loom task list` and `loom ai context` can still work even when the daemon is stopped.

## Common Problems

### Port already in use

```bash
make stop
```

### Toolbelt panel missing

Make sure:

1. iTerm2's Python API is enabled
2. Loom was started from **Scripts -> loom**
3. The Toolbelt entry is enabled from the gear menu

### CLI cannot talk to the daemon

Check:

- the daemon is running
- the port matches `LOOM_PORT`
- the log file contains no startup error

### Browser view does not update

The browser and Toolbelt both use the same WebSocket stream. If one view is stale, refresh it and check the daemon log before assuming the frontend is the problem.

## Environment Variables

Common runtime variables:

| Variable | Description |
|---|---|
| `LOOM_PORT` | HTTP/WebSocket port |
| `LOOM_STANDALONE` | Skip Toolbelt registration when set |
| `LOOM_DEFAULT_CMD` | Default boot command |
| `LOOM_BIND_ALL` | Bind to `0.0.0.0` instead of localhost |

## Related Docs

- [Getting Started](getting-started.md)
- [Group Settings](group-settings.md)
- [CLI Reference](cli.md)
- [Architecture](architecture.md)
