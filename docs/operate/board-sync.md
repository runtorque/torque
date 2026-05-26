# Board sync operator guide

Board sync connects Torque tasks to external planning boards. V1 ships a GitHub Issues + Projects v2 adapter; future Jira, Linear, or other adapters can reuse the same group settings and sync UI with provider-specific configuration.

## Setup

Authenticate GitHub CLI with an account that can edit issues in the repo:

```bash
gh auth status
gh auth login
```

Projects v2 writes require the extra `project` OAuth scope:

```bash
gh auth refresh -s project
```

Open **Group Settings → Group → Sync provider**, choose **GitHub**, fill the
provider fields, click **Test connection**, then enable sync after preflight
passes.

| Setting | Use |
|---|---|
| Repository | `owner/repo`; **Use current repo** fills this from `gh repo view`. |
| Project owner / number | Owner login and Project v2 number from the URL; classic project IDs are not supported. |
| Project Status field name | Single-select field name, usually `Status`. |
| Lane → status mapping | JSON map from Torque lane names to Project Status option names. |
| Close linked issues via PR body | Add PR closing refs; enabled by default. |
| Create missing labels / Assignee map | Auto-create missing GitHub labels by default; assignee map is Torque agent/engineer ID → GitHub login JSON. |

For scripted setup, use `torque group settings ... -s board_sync_provider=github`
with a JSON `board_sync_github` value. See [CLI reference](../reference/cli.md#board-sync).

Preflight verifies `gh`, auth, repo access, and the Project v2 Status field when
configured. Missing project scope errors include `gh auth refresh -s project`.

## Status mapping

Torque maps **lane → Project Status option**. If a task has a non-empty
pipeline `status` and `github_lane_status_map` contains an explicit key for
that status, the status mapping wins; otherwise the lane mapping/fallback is
used. Option names must match the configured Status field exactly, so create
GitHub options such as `Todo`, `In Progress`, `Review`, and `Done` before
enabling writes.

## Push, pull, and apply

When GitHub sync is enabled for a group, Torque automatically pushes local
top-level product tasks after meaningful board mutations: create, title/body,
labels, lane/status, assignee, and external-link changes. Automatic pushes are
debounced and coalesced (roughly a 1s trailing delay with a 5s max window), so a
burst of card edits produces one latest-state GitHub update instead of one API
call per keystroke or drag.

Auto-create is intentionally limited to top-level user/architect product tasks.
Derived pipeline children, review/fix/ask tasks, schedule-created internal
tasks, and other orchestration nodes are not mirrored by default. Use the task
modal's **Track this task for GitHub sync** / explicit task-level sync opt-in if
an excluded task should be mirrored.

Manual **Sync now** remains available from the card context menu, task modal,
and CLI as a force/retry/recovery path. Pull preview and apply remain
operator-gated; there are no automatic inbound webhooks or background polling in
this scope.

```bash
torque board sync test -g backend
torque board sync push fix-login
torque board sync push --group backend
torque board sync pull --preview fix-login
torque --json board sync pull --preview --group backend
```

Pull preview is operator-gated. The UI shows a local-vs-GitHub diff; select
fields and click **Apply selected**. CLI pull is preview/read-only.

## PR closing references

When the group is configured with the GitHub board-sync provider and **Close
linked issues via PR body** is on, `engineer_merge` on the PR path appends a
`Linked Torque issues` section with missing `Closes #123` or
`Closes owner/repo#123` refs. Existing closing refs are not duplicated. This
requires `engineer_merge_mode=pr` or `engineer-choice` without
`force_direct=true`; direct-local merges have no PR body to update.

## Limits and recovery

- GitHub Issues + Projects v2 only; Projects classic is not supported.
- No webhooks or background polling in V1. Remote changes require manual pull
  preview; new external issues are not auto-imported without an operator gate.
- Automatic pushes use an idempotent outbound hash and short-lived GitHub
  metadata caches to reduce no-op API calls. Transient failures such as rate
  limits or network timeouts are retried with bounded backoff; configuration
  errors wait for a new mutation or manual Sync now.
- Failures surface as structured `type`/`provider`/`phase`/`error` data in UI,
  panel events, CLI JSON, and task `board_sync`.
- Common failures: missing `gh`, not logged in, missing `project` scope, repo or
  project not found, missing Status option, missing labels only when label creation
  is explicitly disabled, permission errors, and rate limits. Fix, run **Test connection**,
  then retry sync.
