# CLI Reference

The `loom` CLI lets you manage agents, terminals, tasks, and worktrees from the command line. Install it with:

```bash
make cli
```

This creates a symlink so `loom` is available in your shell. The CLI talks to the running daemon over HTTP for write operations and reads SQLite directly for read-only queries (so commands like `loom task list` work even when the daemon is stopped).

## Global flags

| Flag | Description |
|------|-------------|
| `--port PORT` | Daemon port (default: 18932) |
| `--json` | Output raw JSON |

---

## status

Show groups and agents, or details for a specific agent.

```bash
loom status                       # show all groups and agents
loom status -g backend            # filter by group
loom status -a                    # show all windows
loom status impl-add-auth         # show details for a specific agent
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Filter by group name |
| `-a, --all` | Show all windows (default: current window only) |

Aliases: `st`, `ls`

---

## group

Manage groups.

### group add

```bash
loom group add backend
```

### group remove

```bash
loom group remove backend
```

Alias: `rm`

### group rename

```bash
loom group rename backend api
```

Alias: `mv`

### group settings

Show or update group settings.

```bash
loom group settings backend                              # show settings
loom group settings backend -s git_worktree=true         # enable worktrees
loom group settings backend -s agent_boot_command=claude  # set boot command
```

Pass `-s KEY=VALUE` to update. Multiple `-s` flags are supported. Boolean values use `true`/`false`.

Alias: `g`

---

## agent

Manage agents.

### agent add

```bash
loom agent add my-agent -g backend
loom agent add my-agent -g backend -c claude -d ~/project --color "#3fb950"
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Target group (auto-detected if in a Loom session) |
| `-c, --command` | Boot command |
| `-d, --directory` | Working directory |
| `--profile` | iTerm2 profile |
| `--color` | Tab color (hex) |

### agent remove

```bash
loom agent remove impl-add-auth
```

Accepts ID, slug, or name. Alias: `rm`

### agent focus

Focus the agent's iTerm2 tab.

```bash
loom agent focus impl-add-auth
```

### agent relaunch

Restart a stopped agent.

```bash
loom agent relaunch impl-add-auth
```

### agent move

Move an agent to a different group.

```bash
loom agent move impl-add-auth -g frontend
```

### agent edit

Update an agent's name or tab color.

```bash
loom agent edit impl-add-auth --name review-auth --color "#a371f7"
```

Alias: `a`

---

## terminal

Manage terminals.

### terminal add

```bash
loom terminal add logs -p impl-add-auth                   # child of an agent
loom terminal add shell -g backend                        # standalone in group
loom terminal add tests -p impl-add-auth -c "npm test"    # with boot command
```

| Flag | Description |
|------|-------------|
| `-p, --parent` | Parent agent (creates a child terminal) |
| `-g, --group` | Group (for standalone terminals) |
| `-c, --command` | Boot command |
| `-d, --directory` | Working directory |
| `--profile` | iTerm2 profile |
| `--color` | Tab color (hex) |

### terminal remove

```bash
loom terminal remove impl-add-auth:logs
```

Alias: `rm`

### terminal reparent

Move a terminal to a different parent agent, or detach it.

```bash
loom terminal reparent impl-add-auth:logs -p review-auth   # new parent
loom terminal reparent impl-add-auth:logs --detach          # standalone
```

Aliases: `t`, `term`

---

## send

Send text to an agent or terminal session.

```bash
loom send "git status" -t impl-add-auth
loom send "fix the auth bug" -t impl-add-auth -w    # send and wait for completion
```

| Flag | Description |
|------|-------------|
| `-t, --to` | Target agent or terminal (auto-detected if in a Loom session) |
| `-w, --wait` | Wait until the agent finishes (polls every 2s) |

---

## broadcast

Send a command to all agents in a group.

```bash
loom broadcast backend "git pull"
```

Alias: `bc`

---

## task

Task and ticket management.

### task create

Create a task in the Backlog without launching an agent.

```bash
loom task create "Add dark mode support" -g frontend
loom task create "Fix login bug" -t oneshot/fix -v MODULE=auth
loom task create "Review PR" -l review,urgent
loom task create "Deploy auth changes" --depends-on review-auth
loom task create "Kick off release checklist" --at "tomorrow 09:00"
```

| Flag | Description |
|------|-------------|
| `-g, --group` | Target group |
| `-t, --action` | Action to attach |
| `-v, --var` | Action variables (`KEY=VALUE`, repeatable) |
| `-l, --labels` | Comma-separated labels |
| `--at` | Schedule dispatch for a future time |
| `--depends-on` | Comma-separated task slugs or IDs that must finish first |

Alias: `c`

### task dispatch

Create a task, launch an agent, and send the prompt --- all in one command.

```bash
loom dispatch "Add dark mode" -t feature/implement -g frontend
loom dispatch "Fix flaky test" -t oneshot/fix -v MODULE=auth -w
loom dispatch "Quick fix" -g backend                          # no action, raw task text
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
loom task show add-dark-mode
```

Accepts ID, slug, or title.

### task list

List tasks with optional filters.

```bash
loom task list                          # all tasks by lane
loom task list -l "In Progress"         # only in-progress tasks
loom task list -g backend               # filter by group
loom task list --label review           # filter by label
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
loom task edit add-dark-mode                                # opens $EDITOR
loom task edit add-dark-mode -t "Updated description"       # inline edit
loom task edit add-dark-mode --action feature/implement     # change action
loom task edit add-dark-mode -l feature,priority            # update labels
loom task edit deploy-auth-middleware --depends-on review-auth,run-auth-tests
loom task edit release-checklist --at "2026-04-07T12:00:00Z"
loom task edit deploy-auth-middleware --verify-state pending --verify-mode deploy
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

### task move

Move a task to a different lane.

```bash
loom task move add-dark-mode -l Done
```

Alias: `mv`

### task chain

Show the full pipeline derivation chain for a task.

```bash
loom task chain add-dark-mode
```

Displays the chain with depth, status, lane, and linked agent for each task.

### task resolve

Resolve a human-in-the-loop ask task by sending an answer back to the waiting agent.

```bash
loom task resolve review-auth "Approved. Merge after CI passes."
```

---

## board

Board management (aliases for common task operations).

### board list

```bash
loom board list                 # all tasks by lane
loom board list -l Backlog      # filter by lane
```

Alias: `ls`

### board add

```bash
loom board add "Quick fix needed" -g backend -l "In Progress"
```

### board move

```bash
loom board move fix-login -l Done
```

### board remove

```bash
loom board remove fix-login
```

Alias: `rm`

### board lanes

List all lanes with task counts.

```bash
loom board lanes
```

Alias: `bd`

---

## action

Action management.

### action list

```bash
loom action list
```

Shows all actions from project (`.loom/actions/`) and global (`~/.loom/actions/`) directories, with scope and variable info.

Alias: `ls`

### action show

```bash
loom action show feature/review
```

Displays the action YAML and auto-discovered variables.

### action create

```bash
loom action create my-action
```

Creates a starter action file in `.loom/actions/`.

Alias: `act`

---

## worktree

Git worktree management. See [Worktrees](worktrees.md) for the full guide.

### worktree create

```bash
loom worktree create impl-add-auth
loom worktree create impl-add-auth --relaunch    # relaunch agent in the worktree
```

### worktree remove

```bash
loom worktree remove impl-add-auth
loom worktree remove impl-add-auth --relaunch    # relaunch agent in the original repo
```

Alias: `rm`

### worktree checkpoint

Create a checkpoint commit (snapshot of current changes).

```bash
loom worktree checkpoint impl-add-auth
```

Alias: `cp`

### worktree history

Show checkpoint history for an agent's worktree.

```bash
loom worktree history impl-add-auth
```

Alias: `log`

### worktree rollback

Reset the worktree to a previous checkpoint.

```bash
loom worktree rollback impl-add-auth abc1234
```

Alias: `wt`

---

## ai

Agent reporting commands. These are designed to be called **by AI agents** (e.g., Claude Code) from within a Loom-managed session. The calling agent is auto-detected via the `LOOM_CELL_ID` environment variable.

### ai done

Mark the current task as complete.

```bash
loom ai done
loom ai done -m "Implemented auth with JWT tokens"    # with summary
```

### ai blocked

Signal that the agent is blocked and needs user input.

```bash
loom ai blocked "Need credentials for the staging database"
```

### ai error

Report an unrecoverable error.

```bash
loom ai error "Build fails due to missing dependency"
```

### ai progress

Report progress on the current task (updates the activity detail in the UI).

```bash
loom ai progress "Running test suite (3/5 passing)"
```

### ai verify

Record manual deploy, restart, smoke, and human-validation checkpoints for the current task.

```bash
loom ai verify --state pending --mode deploy --tests "python3 -m unittest"
loom ai verify --deploy-attempted --smoke-done -m "Smoke passed on staging"
loom ai verify --state failed --human "Need PM sign-off after production check"
```

### ai ready

Signal that the agent is done and ready for a new task. Unlike `done`, this also unlinks the agent from the task.

```bash
loom ai ready
```

### ai context

Show the current agent's context (name, group, directory, worktree, linked tasks). Works offline --- reads from the local database.

```bash
loom ai context
```

### ai derive

Create a derived task and dispatch it. See [Actions & Templates](actions.md#derive-to-agent) for details on `--agent` and `--self`.

```bash
loom ai derive "Review the implementation" -t feature/review
loom ai derive "Fix the issues found" -t feature/fix-review --agent impl-add-auth
loom ai derive "Now add tests" -t feature/implement --self
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
loom ai ask "Implementation is done. Should we deploy or add more tests?"
```

---

## pipeline

Pipeline discovery from action transitions.

### pipeline list

```bash
loom pipeline list
```

Lists all pipelines discovered by scanning action `transitions` fields.

### pipeline show

```bash
loom pipeline show feature/implement
```

Shows the pipeline structure with actions and transition conditions.

Alias: `pl`

---

## schedule

Recurring and one-shot task creation plus automatic dispatch.

### schedule create

```bash
loom schedule create weekly-deps \
  -g backend \
  --cron "0 9 * * 1" \
  --task "Weekly dependency update {date}" \
  -t maintenance/deps

loom schedule create release-checklist \
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
loom schedule list
```

Alias: `ls`

### schedule show

```bash
loom schedule show weekly-deps
```

### schedule edit

```bash
loom schedule edit weekly-deps --cron "0 8 * * 1"
loom schedule edit weekly-deps -t maintenance/deps -v MODULE=auth
```

### schedule enable

```bash
loom schedule enable weekly-deps
```

### schedule disable

```bash
loom schedule disable weekly-deps
```

### schedule run

Trigger the schedule immediately and create a fresh task now.

```bash
loom schedule run weekly-deps
```

---

## logs

Tail the daemon log.

```bash
loom logs              # show last 50 lines
loom logs -f           # follow (like tail -f)
```
