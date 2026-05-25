# Manual testing

Run the automated regression suite with `make test`; the target self-sanitizes
`TORQUE_*` runtime variables inherited from worker/standalone shells.

For manual runtime testing:
1. From a non-worker shell, `make deploy`
2. Relaunch the primary app with `make run` (desktop) or `make standalone` + `make open` (browser)
3. Check `torque.log` in the active profile data dir for errors:
   ```
   ~/.torque/profiles/desktop/torque.log      # make run / desktop profile
   ~/.torque/profiles/standalone/torque.log   # make standalone
   ```
4. Test in the primary desktop/browser UI: create groups, add agents, click agent to select → add child terminals, drag terminals between drawer and standalone, remove agent (cascade), relaunch, right-click → edit name/color, keyboard nav (arrows within group, Tab between groups)
5. Test global settings: click gear icon → Settings modal. General > Server: change default command, toggle filter by window, toggle focus new tabs (uncheck → create agent → verify previous session stays selected). General > Board: edit default lanes. Save → restart daemon → confirm settings persist.
   - Test inline task creation: click `+ Add task` → auto-growing textarea appears. Type and Enter → task created. Escape → clears draft. Blur → draft preserved, clicking `+ Add task` again restores text.
   - Test task modal: task textarea auto-grows. Action variable textareas auto-grow. Edit task → action picker pre-selects stored action, vars pre-filled.
   - Test dispatch: task with action → agent receives rendered prompt. Delete action → dispatch → warning dialog → dispatch without action works.
   - Test action editor: open Actions panel → old-format actions should show coalesced `prompt` field. Create new action → `{{ TASK }}` validation on save. Edit prompt → variables auto-discovered below.
6. Verify SQLite persistence: restart daemon, confirm state survives. Check `torque.db` exists in the active profile data dir.
7. Verify delta sync: open browser devtools → Network → WS tab, confirm messages are `type: "delta"` (not full state) after initial connect.
8. Verify CLI offline reads: `make stop`, then `torque task list` — should work without daemon running.
9. For legacy Toolbelt migration work only, verify any already-installed Toolbelt daemon still presents the same state through `make open`; the Makefile no longer installs or updates the old Scripts copy.
10. Test standalone browser mode: `make stop`, then `make standalone` → `make open` → daemon serves the browser UI and starts the PTY supervisor. Check log for "Standalone mode — using PTY supervisor at <sock path>".
11. Test PTY supervisor survival (standalone only): with a standalone daemon and an open xterm.js terminal, trigger a restart via the header's ↻ button or `POST /api/cmd {"cmd":"restart"}`. The browser's terminal WS should reconnect within ~15s and the shell should still be alive (its state preserved). Check `~/.torque/profiles/<profile>/pty_supervisor.log` for supervisor-side traces, and `pty_supervisor.pid` for the running supervisor pid. Recovery if the supervisor dies: `kill $(cat pty_supervisor.pid)` → `rm pty_supervisor.sock pty_supervisor.pid` → relaunch Torque; a red banner at the top of the UI reports `supervisor_unavailable` until it comes back.
