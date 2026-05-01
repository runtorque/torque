# Getting Started

## Prerequisites

- macOS
- iTerm2 with the Python API enabled: **Preferences -> General -> Magic -> Enable Python API**

## Install

```bash
git clone https://github.com/aleksanderarruda/iterm2-agent-orchestration.git
cd iterm2-agent-orchestration
make deps
make install
make cli
```

`make cli` installs `bin/loom` into `~/.local/bin/loom`. Make sure that directory is in your `PATH`.

## Start Loom

1. Open iTerm2.
2. Run **Scripts -> loom**.
3. Open **View -> Show Toolbelt**.
4. Enable **Loom** from the Toolbelt gear menu.

The Loom panel should now appear in the Toolbelt sidebar.

## Open a Wider Browser View

You can open the same UI in a browser while the Toolbelt is running:

```bash
make open
```

For standalone-only mode, where Loom skips Toolbelt registration but still controls iTerm2 externally:

```bash
make standalone
make open
```

See [Operations](operations.md) for the differences between Toolbelt, dual-mode, and standalone-only setups.

## Open the Native Desktop Shell

Loom also ships a native desktop shell built with `pywebview`. Use it when you
want a real desktop window instead of a browser tab.

Install the optional dependency into the iTerm2-managed runtime:

```bash
make desktop-deps
```

Then launch the native app:

```bash
loom desktop
```

That command intentionally uses desktop-specific defaults so it does not
accidentally target the Toolbelt daemon:

- profile: `desktop`
- port: `18933`
- data dir: `~/.loom/profiles/desktop`

If you already started a matching standalone server and want the native shell to
reuse it instead of spawning a desktop-owned child server:

```bash
loom desktop --attach --profile desktop --port 18933
```

Important notes:

- `pywebview` must be installed in the Python runtime that launches the desktop
  shell. With the default Loom install, that means the iTerm2-managed Python
  environment used by `loom desktop`.
- Attach mode only reuses an existing **matching standalone** Loom runtime. It
  will refuse to connect to the iTerm2-hosted Toolbelt daemon or to a
  standalone server with a different profile/data dir.
- The native shell is currently validated on macOS. Loom still depends on iTerm2,
  so Linux and Windows are not yet full operator targets even though
  `pywebview` itself supports them.

## First Working Session

1. Create a group with **+ Group**.
2. Add an agent with **+ New**.
3. Select that agent and add a companion terminal from the drawer below.
4. Click a cell to focus its iTerm2 tab.
5. Use per-agent or per-engineer controls when you need to send follow-up instructions.

See [Sessions](sessions.md) for the full session model and [Keyboard Shortcuts](keyboard-shortcuts.md) for navigation.

## First Task Workflow

Create a task to park work in Backlog:

```bash
loom task create "Investigate the flaky auth test" -g backend
```

Or create and dispatch a new task directly from the CLI:

```bash
loom task dispatch "Investigate the flaky auth test" -g backend -t oneshot/fix
```

If you want the agent to report back into Loom as it works, the prompt postscript will tell it which `loom ai ...` commands are available for that action.

See:

- [Task Board](board.md)
- [Task Lifecycle](task-lifecycle.md)
- [Actions & Templates](actions.md)

## First Action Workflow

Starter actions live in the repository's `actions/` directory. Copy them into your project-local action directory:

```bash
mkdir -p .loom/actions
cp actions/*.yaml .loom/actions/
```

Inspect them from the CLI:

```bash
loom action list
loom action show feature/implement
```

Then dispatch through an action:

```bash
loom task dispatch "Add retry handling to the webhook client" \
  -g backend \
  -t feature/implement
```

## Update Loom

After pulling new changes:

```bash
make deploy
```

Then restart Loom from the **Scripts** menu.

## Logs and Troubleshooting

- Daemon log: `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log`
- Stop a stale daemon: `make stop`
- Check install/runtime status: `make check`

Common issues and runtime guidance are in [Operations](operations.md).
