# Feature parity — Rust Loom vs Python Loom

What ships, what's missing. Grouped by subsystem. Update as items land.

Legend:
- `[x]` shipped
- `[~]` partial / stub
- `[ ]` not started

Last updated: 2026-04-13.

## 0. Vision for the Rust UI

The existing Python UI is a single vertically-stacked panel inside iTerm2's toolbelt. The Rust app is a standalone native macOS window — which means the UI isn't constrained to a narrow strip anymore. The desired shape is a **tileable grid of panels**: the user arranges terminals, the board, actions editor, weaver feed, context view, etc. side-by-side in whatever layout they want.

Concretely, this pushes the UI design in this direction:

- `NSSplitView` nesting, not a single fixed split (sidebar + content). Every content region can be split further.
- Each "panel" is an independent view that reads from `EngineBridge::snapshot()` and dispatches mutations via `EngineBridge::dispatch`. Panels don't know about each other.
- Terminals are panels too — not special. Whatever panel machinery hosts the Board can host a `GhosttyView`.
- A top-level layout descriptor (persisted in `ui_state`) says what's where. At minimum: tree of splits, leaf = panel kind + optional id (e.g. `terminal:agent-xyz`, `board`, `actions`, `weaver:group-abc`).
- The sidebar stays — it's the "what to drop into a pane" source.

This is Phase 10+ work — not blocking first-run interactivity. Current scaffold (sidebar + single content slot) stays usable.

## 1. UI panels (from Python's `static/js/`)

Panel values in Python: `board`, `actions`, `templates`, `context`, `events`, `weaver`, `memory`.

### Window chrome

- [x] NSWindow + menubar (Quit Cmd-Q) — `loom-ui-native/src/appkit.rs`
- [x] NSSplitView with sidebar + content container
- [ ] Multi-pane tileable grid (nested NSSplitViews, persistable layout, panel-kind enum)
- [ ] Drag-to-split panes / close-pane affordance
- [ ] Window-level keyboard shortcuts (`Cmd+1…9` to focus pane N, `Cmd+W` close pane, `Cmd+D` split right, `Cmd+Shift+D` split down)

### Sidebar (groups + agents tree)

- [x] Group list rendering (read-only text)
- [x] Agents under each group with status dot
- [x] Child terminals nested under parent agent
- [x] Selection marker (▶) on selected agent
- [ ] Clickable rows (swap NSTextView for NSOutlineView with data source)
- [ ] Context menu on a row: Rename / Edit / Remove / Relaunch / Duplicate
- [ ] Drag-drop to reorder groups or move an agent across groups
- [ ] Weaver strip (the "Weaver Code · Branch · Directory · Last event" card under a group)
- [ ] "+ New" cell placeholder tile
- [ ] Tab-color / icon / avatar swatches per cell
- [ ] Worktree branch badge with diff stats
- [ ] Activity-detail subtext (e.g. `Session ended (4h ago)`)
- [ ] Per-group filter by window (`global_settings.filter_by_window`)

### Board panel

- [x] Read lanes + tasks from engine (command surface)
- [ ] Native rendering: lane tabs, card grid, swimlane filter
- [ ] Task card with title, labels, action badge, assignee, agent link
- [ ] Derived-task nesting (indented child cards, collapsible)
- [ ] Pipeline status badges (`On Review`, `Awaiting Input`)
- [ ] Inline `+ Add task` row with autogrowing textarea + draft preservation across rerenders
- [ ] `From action` overlay dropdown (grouped by Project/User)
- [ ] Search / filter bar (matches `board_set_filters`)
- [ ] Saved views (`board_set_saved_views`)
- [ ] Lane sort configs (`board_set_lane_sorts`)
- [ ] Card density toggle (`board_set_card_density`)
- [ ] Card context menu: Dispatch / Edit / Move / Archive / View pipeline / Verify
- [ ] Pipeline thread overlay (chain of derived tasks with click-to-focus)
- [ ] Dispatch flow with missing-action warning dialog
- [ ] Scheduled tasks view (`schedule_*` CRUD)

### Actions panel

- [ ] Action dropdown selector (Project / User optgroups)
- [ ] Structured editor with single `prompt` field
- [ ] Jinja2 syntax highlighting for the prompt
- [ ] `{{ TASK }}` validation on save
- [ ] Variable auto-discovery UI
- [ ] Transitions editor (action picker dropdown + "When" textarea)
- [ ] Save / Duplicate / Delete buttons, scope picker
- [ ] Editor ⇄ Pipelines view toggle
- [ ] Pipelines view: pannable / zoomable canvas, SVG bezier edges, BFS layout, back-edge S-curves
- [ ] "Ask" transitions rendered as pill nodes adjacent to source

### Context panel

- [ ] Agent info card: name, slug, group, status, activity
- [ ] Current task info (title, lane, labels)
- [ ] Worktree summary (branch, dirty, diff stats, checkpoint history)
- [ ] Dispatch history / event log for this agent

### Agents / Events panel

- [ ] Event feed (per-cell event log, ring buffer)
- [ ] Filter by cell, by event kind
- [ ] `events_dismiss` button per entry
- [ ] Activity timeline

### Weaver panel

- [ ] Digest feed (messages from `weaver` adapter)
- [ ] Ask / note dialogs
- [ ] `weaver_pause` / `weaver_resume` controls
- [ ] `weaver_flush_now` button
- [ ] Journal viewer
- [ ] Session map browser
- [ ] Settings form (autonomy mode, push intervals, escalation style, enabled events)

### Memory panel

- [ ] Entry list (by entry_type + scope)
- [ ] Publish / pin / unpin / link UI
- [ ] Linked-entries graph

### Templates panel

- [ ] Agent template browser
- [ ] Template editor (boot command, dir, shell, env, profile, tab color)
- [ ] Save / duplicate / delete

### Modals

- [ ] Task create / edit modal (autogrowing textarea, action picker, variable fieldset, assignee, labels, preview prompt)
- [ ] Agent create / edit modal (group settings tab, worktree config, session resume, idle timeout, notifications)
- [ ] Group settings modal (tabbed: Group / Agents / Terminals)
- [ ] Global settings modal (General / Keybindings, with interactive key capture)
- [ ] Action-to-task flow (pre-selects action)
- [ ] Prompt preview modal (scrollable, max-height)
- [ ] Color picker
- [ ] Hint tooltip popovers
- [ ] Custom `showConfirm()` replacement — use `NSAlert` directly on native
- [ ] Artifact upload / gallery UI

### Global affordances

- [ ] Keyboard navigation (arrows within group, Tab between groups, Enter, Delete, N/G/T/B/R shortcuts) — redone as `NSMenuItem` key equivalents + custom responders
- [ ] Broadcast-to-group UI (`Cmd+Shift+B`)
- [ ] macOS notifications (via `notify-rust` or direct `NSUserNotification`)
- [ ] Tab-color equivalent — window accent / sidebar stripe
- [ ] Boot-time parity: first-run check, settings migration prompt
- [ ] `.app` bundle packaging + `Info.plist` with dock icon

## 2. Server commands

Dispatched via `/api/cmd` and in-process `dispatch_command`. Full Python list from `loom/server.py` + `loom/server_*.py`.

### Groups
- [x] `add_group`
- [x] `remove_group`
- [x] `rename_group`
- [x] `move_group`

### Agents + cells
- [x] `add_agent`
- [x] `add_terminal`
- [x] `remove_agent`
- [x] `update_agent`
- [x] `move_agent`
- [x] `reparent_terminal`
- [x] `reorder_child`
- [x] `select_agent`
- [x] `clear_agent_context`
- [ ] `focus_agent` (legacy iTerm2 window focus — may drop)

### Dispatch + AI report
- [x] `dispatch_task` (PTY backend only — no GhosttyView routing yet)
- [x] `ai_report` (progress/done/blocked/error/ready/ask/derive/context)
- [x] `send_text`
- [x] `broadcast_to_group`
- [x] `relaunch_agent`
- [x] `resolve_ask` — human-in-the-loop reply routing
- [x] Dispatch path: route to GhosttyView when the agent is UI-attached

### Board + tasks
- [x] `board_add_task`
- [x] `board_update_task`
- [x] `board_remove_task`
- [x] `board_move_task`
- [x] `board_reorder_task`
- [x] `board_archive_task`
- [x] `board_unarchive_task`
- [x] `board_add_lane`
- [x] `board_rename_lane`
- [x] `board_remove_lane`
- [x] `board_reorder_lanes`
- [x] `task_chain`
- [x] `board_verify_task`
- [x] `board_set_panel`
- [x] `board_set_filters`
- [x] `board_set_saved_views`
- [x] `board_set_lane_sorts`
- [x] `board_set_card_density`
- [ ] `standalone_set_panel_layout` (multi-pane grid layout descriptor — ties into UI §0)

### Actions + templates
- [x] `list_actions`
- [x] `get_action`
- [x] `render_action`
- [x] `save_action`
- [x] `delete_action`
- [x] `preview_prompt`
- [x] `discover_pipelines`
- [x] `list_templates`
- [x] `get_template`
- [x] `save_template` (project or user scope; accepts `config` object or `raw` YAML)
- [x] `delete_template`
- [x] `render_template` (deep-merges overrides onto the template)

### Worktree
- [x] `worktree_create`
- [x] `worktree_remove`
- [x] `worktree_list`
- [x] `worktree_prune`
- [x] `worktree_checkpoint`
- [x] `worktree_history`
- [x] `worktree_diff`
- [x] `worktree_rollback`
- [x] `worktree_check_merge`
- [ ] `worktree_diff_full`
- [ ] `worktree_check_conflicts`
- [ ] `worktree_rebase`
- [ ] `worktree_merge`
- [ ] `worktree_create_pr`
- [ ] `worktree_streams` — implementing/reviewing/fixed/merged stream synthesis
- [ ] `worktree_boundaries` — task-scoped merge boundary detection

### Weaver
- [ ] `weaver_message`
- [ ] `weaver_journal_append`
- [ ] `weaver_journal_read`
- [ ] `weaver_journal_delete`
- [ ] `weaver_session_map_read`
- [ ] `weaver_update_settings`
- [ ] `weaver_ask`
- [ ] `weaver_note`
- [ ] `weaver_dismiss_note`
- [ ] `weaver_reply`
- [ ] `weaver_pause`
- [ ] `weaver_resume`
- [ ] `weaver_flush_now`

### Memory
- [x] `memory_publish`
- [x] `memory_list` (filters: group, project_key, scope_kind, scope_ref, entry_type, task_id, pinned_only, linked_target_kind/ref, search)
- [x] `memory_read`
- [x] `memory_pin`
- [x] `memory_unpin`
- [x] `memory_link` (attach + detach via `detach: true`)
- [x] `db_memory` schema + tables (`memory_entries`, `memory_links`)
- [x] In-memory structs + delta ops (`MemoryUpsert`, `MemoryRemove`)
- [ ] Expiry sweep for `retention_kind: "transient"` entries (not wired yet)

### Playbooks
- [ ] `get_playbooks`
- [ ] `get_playbook`
- [ ] `list_playbook_candidates`
- [ ] `extract_playbook_candidates`
- [ ] `generate_playbook_draft`
- [ ] `publish_playbook_draft`
- [ ] `discard_playbook_draft`

### Scheduling (cron)
- [x] Scheduler tick loop (`scheduler::spawn`, 15s tick, fires `scheduled_at`)
- [x] `schedule_create`
- [x] `schedule_update`
- [x] `schedule_remove`
- [x] `schedule_enable`
- [x] `schedule_disable`
- [x] `schedule_list`
- [x] `schedule_run` (manual kick)
- [ ] Cron expression parsing / next-run computation — `cron_expr` stored but not yet fired on schedule

### External tickets (Jira / GitHub)
- [ ] `external_import_task`
- [ ] `external_link_task`
- [ ] `external_open_task`
- [ ] `external_push_task_status`
- [ ] `external_post_task_comment`
- [ ] `gh` CLI shell-out wrapper

### Artifacts / uploads
- [ ] `task_upload_artifact`
- [ ] `remove_attachment`
- [ ] Multipart upload handler (`uploads.rs` currently stub)
- [ ] Artifact filesystem layout (`attachments_dir`)

### Events
- [ ] `get_events`
- [ ] `get_agent_history`
- [ ] `get_agent_history_detail`
- [ ] `events_dismiss`
- [ ] `EventLog` ring buffer per agent (port from `events.py`)
- [ ] Event bus throttle window (200ms) — current Rust broadcast is unthrottled

### Prompts
- [ ] `get_playbook_candidates`

### Settings + config
- [x] `get_config`
- [x] `get_global_settings`
- [x] `get_group_settings`
- [x] `update_group_settings`
- [x] `update_global_settings`
- [ ] `suspend_keybindings` (may drop — iTerm2-specific)
- [ ] `resume_keybindings` (may drop)
- [ ] `restart` (process self-respawn)

### Meta / admin
- [x] `refresh` / `resync`
- [x] `ping`

## 3. MCP tools

Server exposes these over `/mcp` (JSON-RPC). Agent-scoped (called by dispatched Claude/Codex). Weaver-scoped (called by the Weaver agent). 

### Agent-scoped (`loom/mcp.py`)
- [x] `loom_progress`
- [x] `loom_done`
- [x] `loom_ready`
- [x] `loom_blocked`
- [x] `loom_error`
- [x] `loom_ask`
- [x] `loom_context`
- [x] `loom_derive`
- [x] `loom_verify`
- [x] `loom_name`
- [x] `loom_reply`
- [x] `loom_memory_publish` (auto-stamps source_kind/source_id/source_name)
- [x] `loom_memory_list`
- [x] `loom_memory_read`
- [x] `loom_memory_pin`
- [x] `loom_memory_unpin`
- [x] `loom_memory_link`
- [ ] `loom_task_upload_artifact` (multipart)

### Weaver-scoped (`loom/mcp_weaver_tools/tool_specs.py`)
All not-started. Full list:
- [ ] `weaver_board_summary`
- [ ] `weaver_session_map`
- [ ] `weaver_streams_list`
- [ ] `weaver_stream_show`
- [ ] `weaver_board_list`
- [ ] `weaver_task_show`
- [ ] `weaver_agents_list`
- [ ] `weaver_agent_show`
- [ ] `weaver_actions_list`
- [ ] `weaver_action_show`
- [ ] `weaver_task_create`
- [ ] `weaver_task_edit`
- [ ] `weaver_task_upload_artifact`
- [ ] `weaver_task_verify`
- [ ] `weaver_task_move`
- [ ] `weaver_task_dispatch`
- [ ] `weaver_batch_dispatch`
- [ ] `weaver_task_resolve`
- [ ] `weaver_events`
- [ ] `weaver_launch_settings`
- [ ] `weaver_notifications`
- [ ] `weaver_resume`
- [ ] `weaver_journal`
- [ ] `weaver_journal_read`
- [ ] `weaver_agent_message`
- [ ] `weaver_ask`
- [ ] `weaver_note`
- [ ] `weaver_agent_close`
- [ ] `weaver_agent_relaunch`
- [ ] `weaver_merge`
- [ ] `weaver_rebase`
- [ ] `weaver_create_pr`
- [ ] `weaver_diff`
- [ ] `weaver_worktree_remove`
- [ ] `weaver_worktree_checkpoint`

## 4. Subsystem modules

### Engine (`loom-core`)
- [x] `state.rs` — `MatrixState`, all dataclasses, slugs, delta accumulator
- [x] `delta.rs` — typed `DeltaOp` enum matching Python wire format
- [x] `db.rs` — rusqlite WAL, 8 tables + schedules/weaver, load_all, targeted saves
- [x] `events.rs` — `EventBus` broadcast channel (no throttle yet)
- [x] `task_ids.rs` — ID formatting / parsing
- [x] `artifacts.rs` — attachment validation helpers
- [x] `slug.rs`, `config.rs`
- [ ] Per-cell event ring buffer (`EventLog`)
- [ ] Throttled broadcast (200ms window)
- [ ] `task_health.rs` heuristics (stale / blocked / at-risk)
- [ ] `memory` schema + data model
- [ ] `playbook` data model
- [ ] `external_tickets` data model

### Actions (`loom-actions`)
- [x] YAML load + namespace (subdirectories)
- [x] minijinja env with `StrictUndefined`
- [x] `{{ TASK }}` validation, reserved `loom` var rejection
- [x] Variable discovery via AST walk
- [x] `loom.*` context namespace (agent / context / worktree / task / terminals)
- [x] Transitions + pipeline discovery

### Worktree (`loom-worktree`)
- [x] git2-based ancestry / diff / status
- [x] `worktree add/remove` via shell-out
- [x] Checkpoint ring buffer
- [x] Merge detection (`is_merged`, ancestry + `merge-tree --write-tree`)
- [x] Base-advanced fallback
- [ ] `streams` synthesis (`implementing` / `reviewing` / `fixed` / `merged`)
- [ ] `boundaries` detection (task-scoped merge boundaries)
- [ ] `rebase` / `merge` / `create_pr` shell-outs
- [ ] Conflict analysis (`check_conflicts`)

### Adapters (`loom-adapters`)
- [~] `claude_code.rs` — 57 lines (stub)
- [~] `codex.rs` — 22 lines (stub)
- [~] `gemini.rs` — 15 lines (stub)
- [~] `generic.rs` — 15 lines (stub)
- [ ] Full event parsing + activity inference (Python: ~1.4k LOC combined)
- [ ] Hook install / uninstall (.claude/settings.local.json merge)
- [ ] Session resume (`claude --resume`)
- [ ] MCP config install (Codex path)

### Weaver (`loom-weaver`)
- [~] Crate exists — all files <50 lines, stubs only
- [ ] `weaver.rs` — event buffer, digest construction, system prompt
- [ ] `hints.rs` — next-action / blocked-dependency synthesis
- [ ] `session_map.rs` — agent → cell_id recovery map
- [ ] `task_health.rs` — stale / blocked / at-risk scoring
- [ ] `mcp.rs` — weaver MCP routing
- [ ] `mcp_tools.rs` — all 34 weaver tools (see §3)

### PTY (`loom-pty`)
- [x] `portable-pty` backend, spawn / write / resize / close

### Terminal (`loom-ghostty`)
- [x] Full libghostty FFI (88 fns, 49 structs via bindgen)
- [x] `GhosttyApp` singleton, 6 runtime callbacks
- [x] `Surface` wrapper with `new_macos`, `set_size`, `set_content_scale`, `set_focus`, `draw`, `write_text`, `send_key`
- [x] `GhosttyView` (NSView subclass) with `viewDidMoveToWindow`, `keyDown`/`keyUp`, `setFrameSize:`
- [ ] Mouse events (`mouseDown:` / `mouseMoved:` / `scrollWheel:` → `ghostty_surface_mouse_*`)
- [ ] Full key translation (Cmd+C/V → clipboard callbacks, Return/Tab/arrow via keycode map)
- [ ] Clipboard callbacks (read/write/confirm_read)
- [ ] `flagsChanged:` — modifier events
- [ ] Surface focus tracking across window focus changes

### UI native (`loom-ui-native`)
- [x] `render.rs` sidebar + content (pure Rust)
- [x] `appkit.rs` NSApplication, NSWindow, NSSplitView, menubar, 500ms refresh timer
- [x] Content container that swaps GhosttyView by `selected_agent_id`
- [x] `EngineBridge` with snapshot / dispatch / subscribe
- [x] Per-cell command resolution (`claude` / `/bin/zsh`)
- [ ] All panels from §1

### Scheduler (`loom-server::scheduler`)
- [x] 15s tick loop firing `scheduled_at` tasks
- [ ] Cron expression parsing / next-run computation
- [ ] Timezone support

### MCP handler (`loom-server::mcp`)
- [x] JSON-RPC shell, `initialize` / `tools/list` / `tools/call`
- [x] 8 agent tools dispatched
- [ ] Remaining agent tools + all 34 weaver tools

### Notifications
- [ ] `notify-rust` integration (macOS only for v1)
- [ ] 5s batching window

### CLI compat (Python `bin/loom`)
- [x] Reads SQLite directly (schema is shared)
- [x] HTTP writes via `/api/cmd` (command surface growing)
- [ ] Add Rust daemon's install dir to `get_state_local()` fallback paths

### Claude Code integration
- [ ] `.claude/settings.local.json` hook install on dispatch
- [ ] Session ID persistence for `claude --resume`
- [ ] `/events` hook receiver — currently scaffolded but not routed into `EventLog`

## 5. Infrastructure + polish

- [ ] App bundle (`.app` with Info.plist, dock icon, code-signing)
- [ ] Crash reporter
- [ ] Telemetry opt-in
- [ ] First-run onboarding (no groups → create default)
- [ ] Log rotation
- [ ] Auto-update channel
- [ ] Preferences window
- [ ] About window
- [ ] Lockfile / single-instance enforcement (replace Python `desktop.py`)

## 6. Test debt

- [x] Env-var race fixes (`LOOM_PROJECT_ROOT`, `LOOM_DEFAULT_CMD`, ghostty `SURFACES`)
- [ ] Cross-daemon parity tests (Python vs. Rust action render, dispatch, ai_report)
- [ ] Playwright / E2E tests against Rust server (Python tests already WebSocket-level; should Just Work)
- [ ] `loom-weaver` unit tests once the crate fills out
- [ ] `loom-ui-native::appkit` smoke test (open window, verify snapshot propagation)
