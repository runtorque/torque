# Getting started

Install Torque, run it once, and create your first task. By the end of this page you'll have a working Torque setup, an agent that responded to a task you dispatched, and a board with one Done card on it.

If you haven't read the pitch yet, [What is Torque?](what-is-torque.md) gives you the mental model in two minutes. The rest of this page assumes you want to install it.

## Prerequisites

- macOS
- A Claude Code, Codex, or Gemini CLI install on your `PATH`

Torque itself is the orchestrator; it doesn't ship the agents. You'll need at least one agent CLI installed and authenticated.

## Install

```bash
git clone git@github.com:runtorque/torque.git
cd torque
make deps
make deploy
```

`make deploy` installs the primary standalone/desktop app files under
`~/.torque/app` and installs the `torque` CLI symlink into
`~/.local/bin/torque`. `make deps` creates or repairs the primary Python
runtime at `~/.torque/runtime/venv`. Make sure `~/.local/bin` is on your
`PATH`.

## Start Torque

Start the primary desktop app:

```bash
make run
```

The Torque workspace should appear in a native desktop window. If it doesn't,
check `~/.torque/profiles/default/torque.log` for errors.

## Browser mode and legacy data migration

For standalone browser mode:

```bash
make standalone
make open
```

Use the desktop app or standalone browser mode for new installs. If you have
old Toolbelt data from a pre-removal install, migrate it to a profile with
`scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b). For more runtime
modes, see [Operations](../operate/operations.md).

## Your first session

This is the whole loop you're going to be doing for the rest of your life with Torque. Get comfortable with it.

1. **Create a group.** Click **+ Group** in the Torque workspace and give it a name like `playground`. A group is just a container — think of it as one project area, one feature, one chunk of work you want to keep together.

2. **Add a worker.** Inside the group, click **+ New** and choose Worker. Torque starts a managed PTY session running `claude` (or whatever the default boot command is). The worker's cell appears in the grid.

3. **Click the worker.** The focus pane at the bottom shows you what the worker is doing. The grid cell's status dot tells you whether it's idle, running, or in trouble.

4. **Dispatch a task to it.** From the CLI:

    ```bash
    torque task dispatch "Write hello-world.py with a friendly greeting" -g playground
    ```

   Torque renders the dispatch prompt, sends it to the worker's tab, and links the task to the worker on the board. The worker starts working. When it's done it calls `task_complete(...)` (via MCP) and the task moves to Done.

5. **Watch the board.** Open the board panel (++a++ or click the board icon). You'll see your task in In Progress, then in Done.

That's the full loop. Everything else in Torque — Engineers, Architects, pipelines, threads, schedules — exists to make this loop scale to dozens of tasks running in parallel without you losing track of any of them.

## Your first action

Workers get smarter when you stop typing prompts and start using **actions**. The repo ships with example actions in `actions/`. Copy them into your project:

```bash
mkdir -p .torque/actions
cp actions/*.yaml .torque/actions/
```

Check what landed:

```bash
torque action list
torque action show feature/implement
```

Now dispatch through an action:

```bash
torque task dispatch "Add a simple counter to hello-world.py" \
  -g playground \
  -t feature/implement
```

Torque renders the action template (with the task description filling `{{ TASK }}`, plus all the live `torque.*` context variables), sends the result, and the worker has a much richer set of instructions than it would have gotten from a bare prompt. See [Actions](../tasks/actions.md) for the full picture.

## When to add an Engineer

You don't need an Engineer for the first few tasks. Engineers become valuable once you have more than two workers running at once, or once you start using a pipeline (implement → review → fix-review).

When you're ready, open Group Settings → Engineer and click **Create Engineer**. The Engineer boots with a system prompt and a journal, watches the workers in its group, and starts coordinating. See [Engineers](../team/engineers.md).

## When to add an Architect

You don't need an Architect for one project area. You add an Architect when you're juggling multiple groups, or when product-level planning is starting to leak into your Engineer's day-to-day work.

The Architect is created at the user level (it's not a group-scoped agent). It can hire Engineers into groups for you, draft tasks at the project level, and keep a decision log of choices that span multiple groups. See [Architects](../team/architects.md).

## Updating Torque

After pulling new changes:

```bash
make deploy
```

Then relaunch Torque with `make run` for the desktop app, or run
`make standalone` followed by `make open` for browser-only mode. If you are
migrating from an old Toolbelt install, keep using
`scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b) to move that data
into a profile-backed primary surface.

!!! warning "Don't deploy or stop from inside a Torque-managed worker"
    If you're operating inside a Torque worktree (a tab the daemon spawned), running `make deploy` or `make stop` can kill the very daemon you're talking to and leave the new instance with corrupted in-memory state. The Makefile and HTTP layer both refuse the operation when called from a worker context. See [Operations](../operate/operations.md) for safer alternatives.

## Where to next

- [Core concepts](core-concepts.md) — quick glossary of every term Torque uses.
- [The team model](../team/team-model.md) — Workers, Engineers, Architects.
- [Workflow guide](../operate/workflow-guide.md) — the day-to-day operating loop once you're past the first task.
- [Troubleshooting](../reference/troubleshooting.md) — symptom-first recovery if something feels wrong.
