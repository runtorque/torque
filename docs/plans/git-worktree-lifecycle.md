# Implementation Plan: Git Worktree Lifecycle

**Roadmap phase**: 2 — Git Worktree Lifecycle
**Status**: Core implemented
**Goal**: Make git worktrees a first-class concept in Loom — agents work in isolated branches by default, with checkpoints for rollback, auto-PR creation, and full lifecycle management from the toolbelt.

---

## The Problem

Loom can spawn multiple agents in the same repo. When two agents edit the same files, they collide. The current workaround is manual: the user creates branches and directories themselves. Loom has a basic `git worktree add/remove` path (Phase 1 leftover), but it's a checkbox with no lifecycle — no cleanup on stop, no UI feedback, no checkpoints, no PR creation. Agents need isolated workspaces by default, and Loom should manage the full arc from branch creation to PR merge to cleanup.

## What Already Exists

The Phase 1 codebase has a partial foundation:

| Component | Status | Gaps |
|---|---|---|
| `AgentCell.worktree_path` / `worktree_branch` | Persisted fields | Never displayed in UI |
| `GroupSettings.git_worktree` | Boolean toggle + checkbox in Agents tab | No per-agent override |
| `bridge.create_worktree(cell, base_dir)` | Creates `.worktrees/{cell_id}` with branch `loom/{cell_id}-{slug}` | No error feedback to UI, no validation on restart |
| `bridge.remove_worktree(cell)` | Force-removes worktree + branch | Not called on session termination, only on explicit agent removal |
| `server.py` add_agent handler | Calls `create_worktree` if `gs.git_worktree` is true | Live session's cwd is not updated; worktree is created *after* session starts |
| Relaunch handler | Ignores worktrees entirely | Doesn't reuse or recreate worktree |

---

## Design Principles

1. **Isolation by default** — When worktrees are enabled for a group, every agent gets its own branch and directory. No two agents share a working tree.
2. **Full lifecycle** — Loom manages create → work → checkpoint → PR → merge → cleanup. The user never runs `git worktree` manually.
3. **Visible state** — The toolbelt always shows the worktree branch, checkpoint count, and diff summary. No invisible git state.
4. **Safe cleanup** — Worktrees are never deleted without user confirmation if they contain uncommitted changes. Auto-cleanup only happens for clean worktrees.
5. **Incremental rollout** — Each sub-feature (managed worktrees, checkpoints, auto-PR) works independently. Users opt in per group.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Toolbelt UI                          │
│                                                          │
│  ┌─────────────┐  ┌──────────┐  ┌─────────────────────┐ │
│  │ Branch badge │  │ Diff btn │  │ PR status indicator │ │
│  └──────┬──────┘  └────┬─────┘  └──────────┬──────────┘ │
│         │              │                    │             │
│         └──────────────┼────────────────────┘             │
│                        │ WS commands                     │
└────────────────────────┼─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                   Command Dispatch                       │
│                   (server.py)                            │
│                                                          │
│  create_worktree │ remove_worktree │ checkpoint          │
│  rollback        │ create_pr       │ merge_pr            │
│  show_diff       │ cleanup_worktree                      │
└────────┬─────────────────┬───────────────────────────────┘
         │                 │
┌────────▼──────┐  ┌───────▼────────────────────────────┐
│  WorktreeManager │  │  GitHub CLI (gh)                    │
│  (worktree.py)   │  │  PR create / merge / status         │
│                  │  └────────────────────────────────────┘
│  • create        │
│  • remove        │
│  • checkpoint    │
│  • rollback      │
│  • diff_summary  │
│  • validate      │
└──────────────────┘
```

---

## What Was Built

### WorktreeManager (`loom/worktree.py`)

Dedicated module for all git worktree operations. Methods:

| Method | Purpose |
|---|---|
| `create(cell, repo_root, base_dir, base_branch)` | Create worktree at `.loom/worktrees/{cell_id}`, branch `loom/{name}-{short_id}`. Records `worktree_path`, `worktree_branch`, `worktree_repo_root`, `worktree_base_branch` on the cell. Adds `.loom/` to `.gitignore`. |
| `remove(cell, force)` | Remove worktree + branch via `git -C {repo_root} worktree remove`. Resolves repo root from `cell.worktree_repo_root`, falling back to `get_repo_root()` or parent directory. |
| `validate(cell)` | Check that `worktree_path` exists on disk and is a valid git working tree. |
| `diff_summary(cell)` | `git diff --numstat {base_branch}...HEAD` → `{files, insertions, deletions}`. |
| `has_uncommitted_changes(cell)` | `git status --porcelain` → bool. |
| `checkpoint(cell, message)` | Stage all + commit with `loom: checkpoint N — {name}`. Increments `worktree_checkpoints`. Returns SHA. |
| `count_commits(cell)` | `git rev-list --count {base_branch}..HEAD`. |
| `get_repo_root(directory)` | `git rev-parse --show-toplevel`. |
| `get_current_branch(repo_root)` | `git rev-parse --abbrev-ref HEAD`. |

All git operations use `asyncio.create_subprocess_exec` and specify `-C {repo_root}` to avoid cwd dependency (the Loom daemon runs outside any repo).

### Data Model

**AgentCell extensions:**

| Field | Type | Persisted | Purpose |
|---|---|---|---|
| `worktree_path` | str | Yes | *Pre-existing.* Absolute path to worktree directory |
| `worktree_branch` | str | Yes | *Pre-existing.* Branch name (e.g., `loom/fix-auth-a1b2c3d`) |
| `worktree_repo_root` | str | Yes | *New.* Original repo root (needed for `git -C` on remove/diff) |
| `worktree_base_branch` | str | Yes | *New.* Branch the worktree forked from — used for diff base |
| `worktree_dirty` | bool | No | *New.* Has uncommitted changes (updated by periodic diff task) |
| `worktree_diff` | dict | No | *New.* `{files: int, insertions: int, deletions: int}` |
| `worktree_checkpoints` | int | No | *New.* Number of checkpoint commits on this branch |
| `last_summary` | str | No | *New.* Last assistant message from Claude Code's Stop hook — used for checkpoint commit messages |

**GroupSettings extensions:**

| Field | Type | Default | Purpose |
|---|---|---|---|
| `git_worktree` | bool | False | *Pre-existing.* Enable worktrees for agents in this group |
| `worktree_base_dir` | str | `.loom/worktrees` | *New.* Directory for worktrees (relative to repo root) |
| `worktree_base_branch` | str | `""` | *New.* Branch to fork from (empty = current HEAD) |
| `worktree_auto_checkpoint` | bool | False | *New.* Auto-checkpoint when agent finishes a turn |

### Agent Creation Flow

Fixed the critical ordering bug where the worktree was created *after* the session. New flow:

1. `add_agent` command → create `AgentCell`
2. If `gs.git_worktree` and cell has a directory → `worktree_mgr.get_repo_root()` → `worktree_mgr.create()` → overwrite `cell.directory` with worktree path
3. `bridge.create_session(cell)` — session `cd`s into the worktree, hooks install there, boot command runs

### Relaunch Flow

1. If cell has `worktree_path` → `worktree_mgr.validate(cell)`:
   - Valid → reuse it, set `cell.directory = worktree_path`
   - Invalid (deleted externally) → clear fields, log warning
2. If no worktree and `gs.git_worktree` enabled → create a new one
3. `bridge.create_session(cell)`

### Removal Flow

- `remove_agent` / `remove_group` → `worktree_mgr.remove(cell)` for each cell with a worktree
- UI "Remove Worktree" context menu action shows a confirm dialog that warns about uncommitted changes (`worktree_dirty`)

### Reconnect (Daemon Restart)

`reconnect_orphans()` validates all persisted `worktree_path` values. If the path no longer exists on disk, clears `worktree_path`, `worktree_branch`, `worktree_repo_root`, and `worktree_base_branch`.

### Auto-Checkpoint

Triggered by two events:
- **`EventBus.on_session_end`** — fires when Claude Code's `Stop` hook reports the agent finished its turn (the right moment — agent is done working but the terminal stays open)
- **`bridge.on_session_terminated`** — fires when the iTerm2 tab is actually closed (fallback for catching uncommitted work)

Both call the same `_auto_checkpoint(cell)` function in `server.py`, which checks `gs.worktree_auto_checkpoint` and calls `worktree_mgr.checkpoint()`.

**Important:** Auto-checkpoint fires on `session_end` (hook event), not on session termination (tab close). Claude Code agents return to the shell prompt when they finish — the tab stays open. The `SessionTerminationMonitor` only fires on tab close, which is too late for the normal workflow.

**Checkpoint commit messages:** When Claude Code's `Stop` hook fires, the `last_assistant_message` field is captured and stored as `cell.last_summary`. Checkpoint commits use this as the commit body:

```
loom: checkpoint 3 — Agent 1

I've updated server.py to fix the auth validation bug and added
unit tests in test_auth.py. The login endpoint now properly
validates JWT expiry timestamps.
```

If no summary is available, falls back to just the subject line: `loom: checkpoint 3 — Agent 1`.

### Merge to Main

Delegates the merge to the running Claude Code session. Flow:

1. User clicks "Merge to Main" → confirm dialog: "Claude will perform the merge and resolve any conflicts. You'll be notified if it fails."
2. Loom builds a fixed merge prompt with the branch names, repo root, and merge method (squash or regular). Any additional instructions from `worktree_merge_instructions` group setting are appended. The core prompt can't be broken by user input.
3. Prompt is sent to the session via `bridge.send_text()` with `\r` to submit.
4. Cell ID is added to `_pending_merges` set.
5. When `session_end` fires for a pending-merge cell, `worktree_mgr.is_merged(cell)` runs `git merge-base --is-ancestor` to verify.
6. **Success:** green toast notification: `"Agent 1" merged to main`. Auto-checkpoint is skipped for the merge turn.
7. **Failure:** cell gets `needs_attention = true` with `error_message = "Merge to main failed — merge manually"`. The amber attention badge draws the user's eye.

If the session isn't running, the user gets an error message telling them to relaunch or merge manually.

### Periodic Diff Updater

`_worktree_diff_updater()` runs every 60 seconds. For each cell with a worktree:
- `diff_summary()` → updates `worktree_diff`
- `has_uncommitted_changes()` → updates `worktree_dirty`
- `count_commits()` → updates `worktree_checkpoints`

Broadcasts to the UI only if something changed.

### UI Changes

**Agent cell (`render.js`):**
- Branch badge: `⎇ {branch-name}` with truncation at 14 chars. Shows diff stats (`+N -N`) when there are changes. Dimmed when agent is stopped.

**Context menu (`commands.js`):**
- Agent with worktree: "Worktree ▸" submenu containing:
  - Checkpoint
  - History... (opens commit history modal)
  - Create PR (disabled — not yet implemented)
  - Merge to Main — confirm dialog, sends prompt to Claude Code session, verifies merge via `git merge-base --is-ancestor` when agent finishes
  - Remove Worktree (with dirty confirm dialog)
- Agent without worktree (group has worktrees enabled): "Create Worktree" (prompts relaunch if agent is running)

**History modal (`modals.js`):**
- Timeline view with vertical git-log-style line
- Each commit shows: subject, short SHA, relative date, +/- stats
- HEAD commit tagged with green "HEAD" badge; other commits have a rollback button
- Clicking a commit with a body toggles the full commit message (pre-wrapped text)
- Rollback shows a confirm dialog, then sends `worktree_rollback`

**Group settings modal (`modals.js`, Agents tab):**
- Under "Git worktree per agent" checkbox, a collapsible section with:
  - Worktree directory (default: `.loom/worktrees`)
  - Base branch (placeholder: "main", empty = current HEAD)
  - Auto-checkpoint on stop toggle
  - Squash commits on merge checkbox (default: on)
  - Additional merge instructions textarea (appended to the fixed merge prompt)

**Toast notifications (`commands.js`, `style.css`):**
- Slide-up toast at the bottom of the toolbelt for transient messages
- Success (green border) and error (red border) variants
- Auto-dismisses after 4 seconds

**CSS (`style.css`):**
- `.cell-branch` — accent-colored branch name, centered under agent name
- `.cell-branch.dimmed` — gray when stopped
- `.cell-diff` — green diff stats inline with branch badge

### Server Commands

| Command | Purpose |
|---|---|
| `worktree_create` | Manually create a worktree for an agent that doesn't have one |
| `worktree_remove` | Remove an agent's worktree |
| `worktree_checkpoint` | Manually create a checkpoint commit |
| `worktree_history` | Return list of commits (responds directly to requesting WS client) |
| `worktree_rollback` | Reset worktree branch to a given commit SHA |
| `worktree_merge` | Send merge prompt to Claude Code session, track pending merge for verification |

---

## File Structure

New files:

```
loom/
  worktree.py              # WorktreeManager: create, remove, validate, checkpoint, diff, count
```

Modified files:

```
loom/state.py              # AgentCell +6 fields, GroupSettings +3 fields, ephemeral field list
loom/server.py             # WorktreeManager wiring, lifecycle fixes, 6 commands, diff updater, auto-checkpoint, checkpoint messages, merge verification, toast broadcast
loom/bridge.py             # Removed worktree methods, added on_session_terminated callback, worktree validation on reconnect
loom/events.py             # Added on_session_end callback, last_summary capture
loom/adapters/claude_code.py  # Stop hook: capture last_assistant_message as summary
static/js/render.js        # Branch badge, diff stats in agent cells
static/js/ws.js            # worktree_history message handler
static/js/commands.js      # Worktree submenu (Checkpoint, History, Create PR, Merge, Remove), worktree actions
static/js/modals.js        # Worktree section in group settings, history modal with rollback + expandable body
static/style.css           # .cell-branch, .cell-diff, context menu, history modal styles
webview.html               # Worktree config inputs, history modal HTML
Makefile                   # Copy worktree.py, fix stop target (LISTEN-only)
```

---

## Decisions (Resolved)

1. **Worktree directory location** — `.loom/worktrees/` in the repo root. Added to `.gitignore` automatically.

2. **Branch naming** — `loom/{agent-name}-{short-id}` (e.g., `loom/fix-auth-a1b2c3d`). Human-readable, collision-safe via short-id suffix.

3. **Auto-PR scope** — Deferred to a later phase. PR provider interface will be abstracted (not hardcoded to `gh`).

4. **PR provider** — Abstract PR provider interface to be designed in a future phase. No provider implemented yet.

5. **Checkpoint frequency** — On agent stop only, via `worktree_auto_checkpoint` group setting (default: off). Manual checkpoint available via context menu.

6. **Dirty worktree on removal** — Confirm dialog in the UI warns about uncommitted changes.

7. **Multi-repo** — Groundwork laid with `worktree_repo_root` field. Full multi-repo support deferred.

---

## Future Work

- **Auto-PR creation** — Abstract PR provider interface + GitHub (`gh` CLI) implementation. "Create PR" context menu item, `worktree_auto_pr` group setting, `worktree_pr_url` field on AgentCell. Provider interface should support GitHub, GitLab, Bitbucket without hardcoding.
- **Diff viewer modal** — Read-only diff view in the toolbelt. Server sends `git diff` content; modal renders with line-level red/green coloring.
- **Dirty worktree warning on agent removal** — The `removeAgent` function should check `worktree_dirty` and warn, not just the explicit "Remove Worktree" context menu.
- **Multi-repo worktree sets** — Groundwork laid with `worktree_repo_root` field. Full multi-repo support deferred.
- **Claude Code WorktreeCreate/WorktreeRemove hooks** — Listen for Claude Code's own worktree events to stay in sync. Orthogonal to Loom-managed worktrees.
- **Conflict detection** — Phase 6 (Multi-Agent Coordination). Two agents editing overlapping files in different worktrees.
- **PR status polling** — Check if PR was merged/closed and auto-cleanup worktree.
