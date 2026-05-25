# CLI Reference

The `torque` CLI lets you manage agents, terminals, tasks, and worktrees from the command line. Install it with:

```bash
make cli
```

This creates a symlink so `torque` is available in your shell. The CLI talks to the running daemon over HTTP for write operations and reads SQLite directly for read-only queries (so commands like `torque task list` work even when the daemon is stopped).

## Global flags

| Flag | Description |
|------|-------------|
| `--port PORT` | Daemon port (default: 18932) |
| `--json` | Output raw JSON |

---

## status

Show groups and agents, or details for a specific agent.

```bash
torque status                       # show all groups and agents
torque status -g backend            # filter by group
torque status -a                    # show all windows
torque status impl-add-auth         # show details for a specific agent
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Filter by group name |
| `-a, --all` | Show all windows (default: current window only) |

Aliases: `st`, `ls`

---

## desktop

Launch Torque in the native desktop shell.

```bash
torque desktop
torque desktop --attach --profile desktop --port 18933
torque desktop --python "/path/to/python3"
```

Default desktop values are intentionally profile-scoped:

- profile: `desktop`
- port: `18933`
- data dir: `~/.torque/profiles/desktop`

Attach mode only reuses an existing **matching standalone** Torque server. It
will refuse non-standalone runtimes or standalone servers with a different
profile or data dir.

`pywebview` must be installed in the Python runtime that launches the desktop
shell. For the standard Torque install, run:

```bash
make deps
```

Without `--python`, the CLI prefers `TORQUE_PYTHON_EXECUTABLE`, then the
legacy `TORQUE_DESKTOP_PYTHON` override, then the Torque-owned runtime at
`~/.torque/runtime/venv/bin/python`, then any legacy Toolbelt Python kept for
old installs, and finally the current interpreter.

Offline SQLite reads and `torque logs` default to the primary desktop profile
(`~/.torque/profiles/desktop`). Use `TORQUE_PORT=18932` or `torque --port
18932 ...` for the standalone profile, or set `TORQUE_PROFILE` /
`TORQUE_DATA_DIR` for a custom profile. Legacy Toolbelt DB/log fallback is
only used when no primary profile artifact exists and no explicit
profile/data-dir was requested.

| Flag | Description |
|------|-------------|
| `--attach` | Reuse an existing matching standalone Torque server instead of spawning a child server |
| `--profile` | Desktop profile override |
| `--port` | Desktop standalone port override |
| `--data-dir` | Desktop data-dir override |
| `--python` | Python runtime used to launch the desktop shell |

---

## group

Manage groups.

### group add

```bash
torque group add backend
```

### group remove

```bash
torque group remove backend
```

Alias: `rm`

### group rename

```bash
torque group rename backend api
```

Alias: `mv`

### group settings

Show or update group settings.

```bash
torque group settings backend                              # show settings
torque group settings backend -s git_worktree=true         # enable worktrees
torque group settings backend -s agent_boot_command=claude  # set boot command
torque group settings backend -s 'worktree_symlinks=["etl/**/node_modules",".venv"]'
torque group settings backend -s worktree_symlink_gitignored_paths=true
```

Pass `-s KEY=VALUE` to update. Multiple `-s` flags are supported. Boolean values use `true`/`false`.

Alias: `g`

---

## agent

Manage agents.

### agent add

```bash
torque agent add my-agent -g backend
torque agent add my-agent -g backend -c claude -d ~/project
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Target group (auto-detected if in a Torque session) |
| `-c, --command` | Boot command |
| `-d, --directory` | Working directory |
| `--profile` | Legacy profile label (PTY runtime uses `Default`) |
| `--color` | Legacy color metadata (hex) |

### agent remove

```bash
torque agent remove impl-add-auth
```

Accepts ID, slug, or name. Alias: `rm`

### agent focus

Focus the agent's terminal session.

```bash
torque agent focus impl-add-auth
```

### agent relaunch

Restart a stopped agent.

```bash
torque agent relaunch impl-add-auth
```

### agent move

Move an agent to a different group.

```bash
torque agent move impl-add-auth -g frontend
```

### agent edit

Update an agent's name or legacy color metadata.

```bash
torque agent edit impl-add-auth --name review-auth
```

Alias: `a`

---

## terminal

Manage terminals.

### terminal add

```bash
torque terminal add logs -p impl-add-auth                   # child of an agent
torque terminal add shell -g backend                        # standalone in group
torque terminal add tests -p impl-add-auth -c "npm test"    # with boot command
```

| Flag | Description |
|------|-------------|
| `-p, --parent` | Parent agent (creates a child terminal) |
| `-g, --group` | Group (for standalone terminals) |
| `-c, --command` | Boot command |
| `-d, --directory` | Working directory |
| `--profile` | Legacy profile label (PTY runtime uses `Default`) |
| `--color` | Legacy color metadata (hex) |

### terminal remove

```bash
torque terminal remove impl-add-auth:logs
```

Alias: `rm`

### terminal reparent

Move a terminal to a different parent agent, or detach it.

```bash
torque terminal reparent impl-add-auth:logs -p review-auth   # new parent
torque terminal reparent impl-add-auth:logs --detach          # standalone
```

Aliases: `t`, `term`

---

## send

Send text to an agent or terminal session.

```bash
torque send "git status" -t impl-add-auth
torque send "fix the auth bug" -t impl-add-auth -w    # send and wait for completion
```

| Flag | Description |
|------|-------------|
| `-t, --to` | Target agent or terminal (auto-detected if in a Torque session) |
| `-w, --wait` | Wait until the agent finishes (polls every 2s) |

---

## task

Task and ticket management.

### task create

Create a task in the Backlog without launching an agent.

```bash
torque task create "Add dark mode support" -g frontend
torque task create "Fix login bug" -t oneshot/fix -v MODULE=auth
torque task create "Review PR" -l review,urgent
torque task create "Deploy auth changes" --depends-on review-auth
torque task create "Kick off release checklist" --at "tomorrow 09:00"
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Target group |
| `-t, --action` | Action to attach |
| `-v, --var` | Action variables (`KEY=VALUE`, repeatable) |
| `-l, --labels` | Comma-separated labels |
| `--at` | Schedule dispatch for a future time |
| `--depends-on` | Comma-separated task IDs or legacy aliases that must finish first |

Alias: `c`

### task dispatch

Create a task, launch an agent, and send the prompt --- all in one command.

```bash
torque task dispatch "Add dark mode" -t feature/implement -g frontend
torque task dispatch "Fix flaky test" -t oneshot/fix -v MODULE=auth -w
torque task dispatch "Quick fix" -g backend                     # no action, raw task text
```

| Flag | Description |
|------|-------------|
| `-t, --action` | Action for prompt rendering |
| `-g, --group` | Target group |
| `-v, --var` | Action variables (`KEY=VALUE`, repeatable) |
| `-n, --name` | Agent name override |
| `-c, --command` | Boot command override |
| `-d, --directory` | Working directory override |
| `-l, --labels` | Comma-separated labels |
| `-w, --wait` | Wait for the agent to finish |
| `--no-task` | Create the agent but don't send the task text |

Alias: `d`

### task show

Show full details for a task.

```bash
torque task show add-dark-mode
```

Accepts ID, slug, or title.

### task list

List tasks with optional filters.

```bash
torque task list                          # all tasks by lane
torque task list -l "In Progress"         # only in-progress tasks
torque task list -g backend               # filter by group
torque task list --label review           # filter by label
```

| Flag | Description |
|------|-------------|
| `-l, --lane` | Filter by lane |
| `-g, --group` | Filter by group |
| `-a, --assignee` | Filter by assignee |
| `--label` | Filter by label |

Alias: `ls`

### task edit

Edit a task's fields. Opens `$EDITOR` with the task as YAML, or use inline flags.

```bash
torque task edit add-dark-mode                                # opens $EDITOR
torque task edit add-dark-mode -t "Updated description"       # inline edit
torque task edit add-dark-mode --action feature/implement     # change action
torque task edit add-dark-mode -l feature,priority            # update labels
torque task edit deploy-auth-middleware --depends-on review-auth,run-auth-tests
torque task edit release-checklist --at "2026-04-07T12:00:00Z"
torque task edit deploy-auth-middleware --verify-state pending --verify-mode deploy
```

| Flag | Description |
|------|-------------|
| `-t, --task` | New task description |
| `--action` | Set or change action |
| `-v, --var` | Set action variables (`KEY=VALUE`, repeatable) |
| `-l, --labels` | Comma-separated labels |
| `-g, --group` | Move to different group |
| `--at` | Set or replace a scheduled dispatch time |
| `--depends-on` | Replace the task's dependency list |
| `--verify-mode` | Set verification mode (`deploy` or `restart`) |
| `--verify-state` | Set verification state (`pending`, `attempted`, `passed`, `failed`) |
| `--verify-note` | Set verification notes |
| `--verify-tests` | Set tests run summary |
| `--verify-smoke-done` | Mark manual smoke testing as done |
| `--verify-deploy-needed` | Mark that a deploy is still needed |
| `--verify-deploy-attempted` | Mark that deploy or restart was attempted |
| `--verify-human` | Set what still needs human validation |

Alias: `e`

### task verify

Record a deploy/restart verification checkpoint without opening the full task editor.

```bash
torque task verify deploy-auth-middleware --mode deploy --state pending --note "Waiting for staging deploy"
torque task verify deploy-auth-middleware --mode deploy --attempted --note "Deployed to staging"
torque task verify deploy-auth-middleware --smoke passed --note "Login and billing pages load"
torque task verify deploy-auth-middleware --smoke failed --note "Smoke failed on login redirect"
torque task verify deploy-auth-middleware --clear-deploy-needed --clear-human --smoke passed
```

| Flag | Description |
|------|-------------|
| `--mode` | Set verification mode (`deploy` or `restart`) or clear it with `clear` |
| `--state` | Set explicit verification state (`pending`, `attempted`, `passed`, `failed`) or clear it with `clear` |
| `--attempted` | Mark that deploy or restart was attempted and move state to `attempted` unless overridden |
| `--clear-attempted` | Clear the recorded attempted flag |
| `--smoke` | Record smoke result (`passed` or `failed`), or clear smoke-complete with `clear` |
| `--note` | Set verification notes |
| `--clear-note` | Clear verification notes |
| `--tests` | Set tests run summary |
| `--clear-tests` | Clear the tests run summary |
| `--deploy-needed` | Mark that a deploy is still needed |
| `--clear-deploy-needed` | Clear the deploy-needed flag |
| `--human` | Set what still needs human validation |
| `--clear-human` | Clear what still needs human validation |

### task move

Move a task to a different lane.

```bash
torque task move add-dark-mode -l Done
```

Alias: `mv`

### task chain

Show the full pipeline derivation chain for a task.

```bash
torque task chain add-dark-mode
```

Displays the chain with depth, status, lane, and linked agent for each task.

### task resolve

Resolve a human-in-the-loop ask task by sending an answer back to the waiting agent.

```bash
torque task resolve review-auth "Approved. Merge after CI passes."
```

---

## board

Board management (aliases for common task operations).

### board list

```bash
torque board list                 # all tasks by lane
torque board list -l Backlog      # filter by lane
```

Alias: `ls`

### board add

```bash
torque board add "Quick fix needed" -g backend -l "In Progress"
```

### board move

```bash
torque board move fix-login -l Done
```

### board archive

```bash
torque board archive fix-login
```

### board unarchive

```bash
torque board unarchive fix-login
```

### board remove

```bash
torque board remove fix-login
```

Alias: `rm`

### board sync

Structured external-board synchronization. The legacy/manual external-ticket
commands (`board import/link/open/push/comment`) remain available; `board sync`
uses the configured board-sync provider for the group. See the
[operator guide](../operate/board-sync.md) for setup and recovery.

```bash
torque group settings backend -s \
  board_sync_provider=github \
  board_sync_enabled=true \
  'board_sync_github={"github_repo":"owner/repo","github_project_owner":"org","github_project_number":12,"github_lane_status_map":{"Backlog":"Todo","In Progress":"In Progress","Done":"Done"}}'
torque board sync test -g backend
torque board sync push fix-login
torque board sync push --group backend
torque board sync pull --preview fix-login
torque board sync pull --preview --group backend
```

| Command | Description |
|---|---|
| `board sync test -g GROUP` | Run provider preflight checks for a group. |
| `board sync push TASK` | Queue one task for provider sync. |
| `board sync push --group GROUP` | Queue every task in a group for provider sync. |
| `board sync pull --preview TASK` | Preview remote changes for one task. |
| `board sync pull --preview --group GROUP` | Preview remote changes for each task in a group. |

The CLI pull command is read-only. To apply inbound fields, open the card or
task modal in the UI, run **Pull preview**, select fields, and click **Apply
selected**. Use the top-level `--json` flag (for example,
`torque --json board sync pull --preview --group backend`) to inspect structured
`provider`, `phase`, and `error` fields when recovering auth, scope, repo,
project, Status option, label, or rate-limit failures.

### board lanes

List all lanes with task counts.

```bash
torque board lanes
```

Alias: `bd`

---

## action

Action management.

### action list

```bash
torque action list
```

Shows all actions from project (`.torque/actions/`) and global (`~/.torque/actions/`) directories, with scope and variable info.

Alias: `ls`

### action show

```bash
torque action show feature/review
```

Displays the action YAML and auto-discovered variables.

### action create

```bash
torque action create my-action
```

Creates a starter action file in `.torque/actions/`.

Alias: `act`

---

## worktree

Git worktree management. See [Worktrees](../tasks/worktrees.md) for the full guide.

### worktree create

```bash
torque worktree create impl-add-auth
torque worktree create impl-add-auth --relaunch    # relaunch agent in the worktree
```

### worktree remove

```bash
torque worktree remove impl-add-auth
torque worktree remove impl-add-auth --relaunch    # relaunch agent in the original repo
```

Alias: `rm`

### worktree checkpoint

Create a checkpoint commit (snapshot of current changes).

```bash
torque worktree checkpoint impl-add-auth
```

Alias: `cp`

### worktree history

Show checkpoint history for an agent's worktree.

```bash
torque worktree history impl-add-auth
```

Alias: `log`

### worktree rollback

Reset the worktree to a previous checkpoint.

```bash
torque worktree rollback impl-add-auth abc1234
```

Alias: `wt`

---

## ai

Agent reporting commands. These are designed to be called **by AI agents** (e.g., Claude Code) from within a Torque-managed session. The calling agent is auto-detected via the `TORQUE_CELL_ID` environment variable.

### ai done

Mark the current task as complete.

```bash
torque ai done
torque ai done -m "Implemented auth with JWT tokens"    # with summary
```

### ai blocked

Signal that the agent is blocked and needs user input.

```bash
torque ai blocked "Need credentials for the staging database"
```

### ai error

Report an unrecoverable error.

```bash
torque ai error "Build fails due to missing dependency"
```

### ai progress

Report progress on the current task (updates the activity detail in the UI).

```bash
torque ai progress "Running test suite (3/5 passing)"
```

### ai verify

Record manual deploy, restart, smoke, and human-validation checkpoints for the current task.

```bash
torque ai verify --state pending --mode deploy --tests "python3 -m unittest"
torque ai verify --deploy-attempted --smoke-done -m "Smoke passed on staging"
torque ai verify --state failed --human "Need PM sign-off after production check"
```

### ai ready

Signal that the agent is done and ready for a new task. Unlike `done`, this also unlinks the agent from the task.

```bash
torque ai ready
```

### ai context

Show the current agent's context (name, group, directory, worktree, linked tasks). Works offline --- reads from the local database.

```bash
torque ai context
```

### ai derive

Create a derived task and dispatch it. See [Actions & Templates](../tasks/actions.md#transition-targeted-routing) for details on `--agent` and `--self`.

```bash
torque ai derive "Review the implementation" -t feature/review
torque ai derive "Fix the issues found" -t feature/fix-review --agent impl-add-auth
torque ai derive "Now add tests" -t feature/implement --self
```

| Flag | Description |
|------|-------------|
| `-t, --action` | Action for the derived task |
| `-v, --var` | Action variables (`KEY=VALUE`, repeatable) |
| `-g, --group` | Target group (defaults to current task's group) |
| `--agent` | Dispatch to an existing agent by slug, name, or ID |
| `--self` | Dispatch to the calling agent (same session) |

### ai ask

Create a task in Backlog for human review (human-in-the-loop gate).

```bash
torque ai ask "Implementation is done. Should we deploy or add more tests?"
```

---

## pipeline

Pipeline discovery from action transitions.

### pipeline list

```bash
torque pipeline list
```

Lists all pipelines discovered by scanning action `transitions` fields.

### pipeline show

```bash
torque pipeline show feature/implement
```

Shows the pipeline structure with actions and transition conditions.

Alias: `pl`

---

## schedule

Recurring and one-shot task creation plus automatic dispatch.

### schedule create

```bash
torque schedule create weekly-deps \
  -g backend \
  --cron "0 9 * * 1" \
  --task "Weekly dependency update {date}" \
  -t maintenance/deps

torque schedule create release-checklist \
  -g ops \
  --at "tomorrow 09:00" \
  --task "Release checklist {datetime}"
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Target group |
| `--task` | Task title template; supports `{date}`, `{time}`, and `{datetime}` |
| `-d, --context` | Longer task description |
| `--cron` | Recurring 5-field cron expression |
| `--at` | One-shot run time |
| `-t, --action` | Action attached to each created task |
| `-v, --var` | Action variables (`KEY=VALUE`, repeatable) |
| `-l, --labels` | Comma-separated labels |
| `--tz` | IANA timezone for cron evaluation |

### schedule list

```bash
torque schedule list
```

Alias: `ls`

### schedule show

```bash
torque schedule show weekly-deps
```

### schedule edit

```bash
torque schedule edit weekly-deps --cron "0 8 * * 1"
torque schedule edit weekly-deps -t maintenance/deps -v MODULE=auth
```

### schedule enable

```bash
torque schedule enable weekly-deps
```

### schedule disable

```bash
torque schedule disable weekly-deps
```

### schedule run

Trigger the schedule immediately and create a fresh task now.

```bash
torque schedule run weekly-deps
```

---

## logs

Tail the daemon log.

```bash
torque logs              # show last 50 lines
torque logs -f           # follow (like tail -f)
```
