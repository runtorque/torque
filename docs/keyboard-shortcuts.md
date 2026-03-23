# Keyboard Shortcuts

Loom provides two layers of keyboard shortcuts: **Toolbelt shortcuts** that work when the Toolbelt panel is focused, and **global shortcuts** that work from any iTerm2 tab.

## Toolbelt shortcuts

These work when the Loom panel in the Toolbelt has focus. Click anywhere in the panel to focus it.

### Navigation

| Key | Action |
|-----|--------|
| ++up++ | Move focus up within the current group |
| ++down++ | Move focus down within the current group (opens the terminal drawer if an agent is focused) |
| ++left++ | Move focus to the previous agent in the group |
| ++right++ | Move focus to the next agent in the group |
| ++tab++ | Jump to the next group |
| ++shift+tab++ | Jump to the previous group |
| ++enter++ | Activate the focused item (focus its iTerm2 tab) |

### Actions

| Key | Action |
|-----|--------|
| ++n++ | Quick-add an agent to the focused group |
| ++t++ | Quick-add a terminal to the selected agent |
| ++g++ | Create a new group |
| ++b++ | Toggle the broadcast bar for the focused group |
| ++r++ | Relaunch the focused agent or terminal (if stopped) |
| ++delete++ or ++backspace++ | Remove the focused agent or terminal |
| ++escape++ | Close any open modal, menu, or broadcast bar |

## Global shortcuts

These work from any iTerm2 tab, even when the Toolbelt is not focused. They are registered as global key bindings when Loom starts and removed on shutdown.

| Shortcut | Action |
|----------|--------|
| ++cmd+option+down++ | Focus the next managed cell (agent or terminal) |
| ++cmd+option+up++ | Focus the previous managed cell |
| ++cmd+option+right++ | Focus the next agent (skips terminals) |
| ++cmd+option+left++ | Focus the previous agent |
| ++cmd+shift+b++ | Toggle the broadcast bar for the current group |

!!! tip
    The global arrow shortcuts cycle through all managed sessions in the current window, ordered by group position. This means you can navigate between agents and their terminals without touching the Toolbelt panel.

!!! note
    Loom saves any existing key bindings that conflict with its shortcuts and restores them when the plugin is stopped or restarted. If Loom crashes, stale bindings are cleaned up on next startup.
