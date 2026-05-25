# Operations

This page covers the operator-facing side of Torque: how it runs, how to update it, where to look when it breaks, and which runtime modes exist.

## Runtime Modes

### Native desktop shell

This is the primary day-to-day mode. Torque runs in a real native window
through `pywebview`, backed by a standalone daemon:

```bash
make deps
make deploy
make run
```

By default, the desktop shell starts its own standalone Torque server with
desktop-specific runtime values:

- profile: `desktop`
- port: `18933`
- data dir: `~/.torque/profiles/desktop`

### Standalone browser mode

Run Torque with the PTY-backed daemon and open the UI in a browser:

```bash
make standalone
make open
```

Runtime data defaults to `~/.torque/profiles/standalone`.

### Legacy Toolbelt data migration

Older releases shipped a Toolbelt integration and stored data under the old
Scripts tree. The Makefile no longer installs, updates, or launches that copy.
The primary surfaces are the desktop app (`make run`) and standalone browser
mode (`make standalone`). Migrate old Toolbelt data to a profile with
`scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b).

### Desktop attach mode

If you already have a matching standalone server and want the native shell to
reuse it, attach explicitly:

```bash
torque desktop --attach --profile desktop --port 18933
# or
make desktop-attach
```

Attach mode is intentionally conservative:

- it only reuses an existing **standalone** Torque server
- it refuses non-standalone runtimes
- it refuses to attach when the profile or data dir does not match
- it never shuts down an external server that it did not spawn

Spawn mode owns the child server lifecycle and stops that child when the native
window closes. Attach mode only owns the window.

## Install and Update

Initial primary standalone/desktop install:

```bash
make deps
make deploy
```

Update after pulling new changes:

```bash
make deploy
```

Then relaunch with `make run` for the desktop app, or `make standalone` +
`make open` for browser-only mode.

Useful maintenance targets:

- `make stop`
- `make check`
- `make uninstall`
- `make restart`
- `make run`
- `make standalone`
- `make desktop-deps` (compatibility alias for `make deps`)
- `make desktop`
- `make desktop-attach`

## Logs

Primary standalone/desktop logs live in the active profile data dir:

```text
~/.torque/profiles/desktop/torque.log
~/.torque/profiles/standalone/torque.log
```

Legacy Toolbelt data from older releases may still include a log at:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log
```

Tail the active/default profile from the CLI:

```bash
torque logs
torque logs -f
```

By default the CLI targets the desktop profile (`~/.torque/profiles/desktop`).
Use `TORQUE_PORT=18932` / `torque --port 18932 ...`, `TORQUE_PROFILE`, or
`TORQUE_DATA_DIR` to inspect a standalone or custom profile. If the primary
profile log is absent and no explicit profile/data-dir was requested, the CLI
can fall back to an existing legacy Toolbelt log for migration diagnostics.

## Notifications

Torque supports macOS notifications for agent activity. Notification behavior is configured per group.

Available toggles include:

- master notifications switch
- notify on finish
- notify on error
- notify on attention-needed

These settings live in [Group Settings](group-settings.md).

## Runtime State

Persistent state lives in SQLite. Primary standalone/desktop profiles use:

```text
~/.torque/profiles/desktop/torque.db
~/.torque/profiles/standalone/torque.db
```

Legacy Toolbelt data from older releases may still include a database at:

```text
~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db
```

Read-only CLI commands can fall back to SQLite directly, which is why commands like `torque task list` and `torque ai context` can still work even when the daemon is stopped. Offline reads use the desktop profile by default, or the selected profile/data-dir when you pass `TORQUE_PORT=18932`, `--port 18932`, `TORQUE_PROFILE`, or `TORQUE_DATA_DIR`. Legacy Toolbelt DB fallback is only for non-explicit migration diagnostics when no primary profile DB exists.

## Common Problems

### Port already in use

```bash
make stop
# for the default desktop port:
make stop TORQUE_PORT=18933
```

### Old Toolbelt data is missing from the primary app

The primary recovery path is to migrate Toolbelt data to a profile and relaunch
with the desktop app or standalone browser mode. Migration is manual and
non-destructive; `make deploy` does not delete or move old AppSupport files:

```bash
scripts/migrate_toolbelt_to_profile.py
```

Run `torque doctor` to see the `[runtime_locations]` section and warnings for
legacy Toolbelt data or legacy AppSupport Python runtimes.

### CLI cannot talk to the daemon

Check:

- the daemon is running
- the port matches `TORQUE_PORT`
- the log file contains no startup error

### Browser or desktop view does not update

The browser and desktop shell use the same WebSocket stream within a given
daemon. If one view is stale, refresh it and check the daemon log before
assuming the frontend is the problem.

### Native desktop shell will not launch

Check these first:

1. Run `make check` and confirm `pywebview` is installed.
2. Remember that `pywebview` must be installed in the **runtime launching the desktop shell**. On a standard source install, `make deps` installs it into Torque's owned runtime at `~/.torque/runtime/venv`. `make desktop-deps` remains a compatibility alias.
3. If you passed `--python` or `TORQUE_DESKTOP_PYTHON`, install `pywebview` into that interpreter too.
4. If attach mode fails, confirm the target server is standalone and that its `TORQUE_PROFILE`, `TORQUE_PORT`, and `TORQUE_DATA_DIR` match the desktop shell values exactly.

## Environment Variables

Common runtime variables:

| Variable | Description |
|---|---|
| `TORQUE_PORT` | HTTP/WebSocket port |
| `TORQUE_STANDALONE` | Force standalone mode/profile defaults when set |
| `TORQUE_DEFAULT_CMD` | Default boot command |
| `TORQUE_BIND_ALL` | Bind to `0.0.0.0` instead of localhost |
| `TORQUE_DESKTOP_MODE` | Desktop shell lifecycle mode: `spawn` or `attach` |
| `TORQUE_DESKTOP_PROFILE` | Desktop shell profile override (defaults to `desktop`) |
| `TORQUE_DESKTOP_PORT` | Desktop shell port override (defaults to `18933`) |
| `TORQUE_DESKTOP_DATA_DIR` | Desktop shell data-dir override |
| `TORQUE_DESKTOP_PYTHON` | Python runtime used by `torque desktop` |
| `TORQUE_PYTHON_EXECUTABLE` | General Python override preferred by `torque desktop` discovery |
| `TORQUE_BASE_PYTHON` | Base Python used by `make deps` to create `~/.torque/runtime/venv` |
| `TORQUE_RUNTIME_PYTHON` | Primary runtime Python used by Makefile launch targets |

## Platform Expectations

- **Validated now:** macOS desktop/standalone operation with the PTY supervisor.
- **Browser-only smoke:** the served UI path can also be exercised in a browser, but that does not prove native-window behavior.
- **Not yet a supported operator target:** Linux and Windows. The terminal runtime is PTY-based, but the desktop shell, packaging, and operator workflows have not been validated there yet.

## Related Docs

- [Getting Started](../foundations/getting-started.md)
- [Group Settings](group-settings.md)
- [CLI Reference](../reference/cli.md)
- [Architecture](../architecture.md)
