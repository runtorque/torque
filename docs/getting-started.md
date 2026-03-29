# Getting Started

## Prerequisites

- **iTerm2** with the Python API enabled (Preferences > General > Magic > Enable Python API)
- **macOS** (iTerm2 is macOS-only)

## Installation

Clone the repository:

```bash
git clone https://github.com/aleksanderarruda/iterm2-agent-orchestration.git
cd iterm2-agent-orchestration
```

Install dependencies into iTerm2's bundled Python:

```bash
make deps
```

Install the plugin files:

```bash
make install
```

## Running

1. Open iTerm2
2. Go to **Scripts** menu and click **loom**
3. Open the Toolbelt: **View > Show Toolbelt** (++cmd+shift+b++)
4. In the Toolbelt gear menu, check **Loom**

The Loom panel appears in the Toolbelt sidebar.

### Standalone mode

Loom can also run as a standalone browser window instead of the Toolbelt. This mode exists to support terminal emulators other than iTerm2 in the future, but you can also use it alongside the Toolbelt for a wider view.

```bash
# Run without Toolbelt registration
make standalone
make open

# Or just open a browser alongside the running Toolbelt
make open
```

## CLI

Install the `loom` command-line tool:

```bash
make cli
```

This symlinks `bin/loom` to `~/.local/bin/loom`. Make sure `~/.local/bin` is in your `PATH`.

The CLI lets you control Loom from any terminal:

```bash
loom status              # show agents in the current window
loom status --all        # show all agents across all windows
loom agent add my-agent  # create an agent (group auto-detected)
loom send "fix the bug"  # send text to the parent agent
loom send "fix it" --wait # send and wait for the agent to finish
loom logs -f             # tail the daemon log
```

### Dispatch

Create an agent from an action and send it a task in one command:

```bash
loom dispatch "Fix the login bug" --action bugfix --wait
loom dispatch "Fix it" -t bugfix -v TEST_COMMAND=pytest --wait
```

Actions are Jinja2+YAML files. Loom looks in two locations (project-local takes precedence):

- **Project**: `.loom/actions/` in your repo root
- **Global**: `~/.loom/actions/` for actions shared across projects

Variables work anywhere in the file and are auto-discovered — no declaration needed. Defaults come from `| default()` filters. Copy the starters into your repo:

```bash
mkdir -p .loom/actions
cp actions/*.yaml .loom/actions/
```

Or install them globally:

```bash
mkdir -p ~/.loom/actions
cp actions/*.yaml ~/.loom/actions/
```

Manage actions with `loom action list`, `loom action show <name>`, and `loom action create <name>`. The toolbelt also has a "From Action" option in the New Agent dropdown.

Run `loom --help` or `loom <command> --help` for the full reference.

## Auto-launch

To start Loom automatically when iTerm2 opens:

```bash
make autolaunch
```

## First steps

### Create a group

Click **+ Group** in the header bar, or press ++g++. Give it a name that represents your task or project.

### Add an agent

Click the **+ New** button inside the group grid. This creates a new iTerm2 tab running the default boot command (`claude`). The agent appears as a cell in the grid.

For more control, click the dropdown arrow next to **+ New** and select **Custom...** to choose a name, directory, profile, and more.

### Add a terminal

Select an agent by clicking it, then click **New terminal** in the drawer that appears below. The terminal opens as a child of that agent --- removing the agent later will also remove its terminals.

### Navigate

Click any agent or terminal to focus its iTerm2 tab. You can also use [keyboard shortcuts](keyboard-shortcuts.md) to navigate without leaving the terminal.

### Broadcast

Click the broadcast button (++cmd++) on a group header, or press ++b++, to send a command to all sessions in the group at once.

### Clean up

When you're done with a task, click the ++x++ button on an agent to remove it and all its child terminals. Remove an empty group the same way.

## Updating

After pulling new changes:

```bash
make deploy
```

Then restart from the **Scripts** menu.

## Troubleshooting

**Port conflict on startup**: Another instance may be running. Run `make stop` first.

**Plugin doesn't appear in Toolbelt**: Make sure you ran the script from the Scripts menu and checked "Loom" in the Toolbelt gear menu.

**Check logs**: Errors are logged to:

```
~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log
```
