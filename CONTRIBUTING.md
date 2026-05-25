# Contributing

Thanks for your interest in Torque. This project is still early, but thoughtful
issues, documentation fixes, tests, and focused code changes are welcome.

Please also read and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development environment

Start with the setup path in the [README](README.md#quickstart) and the
[Getting Started guide](docs/foundations/getting-started.md). The primary
supported development target is macOS desktop/standalone operation with the
PTY-backed terminal runtime.

The canonical install path is:

```bash
git clone git@github.com:runtorque/torque.git
cd torque
make deps
make deploy
make desktop-deps
```

Useful local commands:

- `make check` verifies the primary app copy and runtime deps.
- `make run` launches the primary desktop app.
- `make standalone` starts a browser-only Torque server backed by the PTY supervisor.
- `make open` opens the web UI for the current Torque port.
- `make desktop-deps` installs the optional native desktop shell dependency.

Torque has no frontend build step: edit `webview.html`, `static/js/*`, and
`static/style.css` directly. Script order in `webview.html` matters. When
changing live frontend panels, preserve operator state across routine rerenders
(scroll position, focus/caret, expanded sections, and drafts) unless the view is
intentionally navigating away.

## Tests and lint

Run the regression suite before opening a pull request:

```bash
make test
```

This runs the Python `unittest` suite and the frontend regression tests wired
through the repo test entrypoint. See [docs/testing.md](docs/testing.md) for the
current coverage map and targeted commands.

There is no separate lint command today. Prefer small, idiomatic changes that
match nearby code style; if a lint target is added later, this section will be
updated to include it.

## Branches and commits

For human-authored branches, use a short descriptive prefix:

- `feature/<short-name>` for user-facing additions
- `fix/<short-name>` for bug fixes
- `docs/<short-name>` for documentation-only changes

Torque-managed agent worktrees use branches such as
`torque/<engineer-slug>/<task-slug>-<shortid>`. You do not need to use that shape
for ordinary contributor branches.

Commit messages should be short, imperative, and specific, for example
`Add task dependency smoke coverage` or `Fix worktree cleanup prompt`. Use the
body when the change needs context: explain why the change is needed, any
tradeoffs, and follow-up work. Co-author trailers are fine when appropriate.

## Pull request expectations

A good PR is focused and easy to verify. Before requesting review:

- Keep the diff scoped to one bug, feature, or documentation improvement.
- Run `make test` and include the result in the PR description.
- Update docs when behavior, commands, settings, or workflows change.
- Link the related issue, discussion, or task when one exists.
- Call out user-visible risks, migration notes, or manual verification steps.
- Get reviewer or maintainer sign-off before merge. No DCO or CLA trailer is
  required today.
- Avoid unrelated formatting churn or cleanup in feature/fix PRs.

Changes to persisted state, task/action behavior, worktrees, MCP tools, or the
live frontend usually need matching tests and documentation updates.

## Issues and feature requests

Use the issue templates under `.github/ISSUE_TEMPLATE/` when filing bugs or
feature requests. If templates are not visible yet, include:

- what you expected to happen
- what actually happened
- steps to reproduce the issue
- your macOS, Python, and Torque version or commit
- relevant logs, screenshots, or terminal output

For feature requests, describe the workflow you are trying to support, why the
current behavior is not enough, and any alternatives you considered.

## Architecture primer

If you are extending Torque's agent shapes or orchestration behavior, read
[docs/concepts.md](docs/concepts.md) first. In short:

- **Groups** organize related agents and terminals.
- **Agents** are AI coding sessions, often isolated in git worktrees.
- **Terminals** are companion shells owned by agents or groups.
- **Actions** are reusable prompt templates for dispatched tasks.
- **Engineer** is the per-group orchestrator that coordinates streams of work.

For deeper implementation context, continue with
[docs/architecture.md](docs/architecture.md), [docs/actions.md](docs/actions.md),
[docs/worktrees.md](docs/worktrees.md), and [docs/engineer.md](docs/engineer.md).
