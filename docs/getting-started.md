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

## First Working Session

1. Create a group with **+ Group**.
2. Add an agent with **+ New**.
3. Select that agent and add a companion terminal from the drawer below.
4. Click a cell to focus its iTerm2 tab.
5. Use the group broadcast button to send the same command to every session in the group.

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
