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
| Create missing labels / Assignee map | Optional label creation and Torque ID/slug → GitHub login JSON. |

For scripted setup, use `torque group settings ... -s board_sync_provider=github`
with a JSON `board_sync_github` value. See [CLI reference](../reference/cli.md#board-sync).

Preflight verifies `gh`, auth, repo access, and the Project v2 Status field when
configured. Missing project scope errors include `gh auth refresh -s project`.

## Status mapping

Torque maps **lane → Project Status option**. If a lane is not in
`github_lane_status_map`, the lane name itself is used. Option names must match
the configured Status field exactly, so create GitHub options such as `Todo`,
`In Progress`, and `Done` before enabling writes.

## Push, pull, and apply

Use a card context menu or the task modal to create/push the GitHub issue, sync
now, pull-preview, or toggle **Track this task for GitHub sync**.

```bash
torque board sync test -g backend
torque board sync push fix-login
torque board sync push --group backend
torque board sync pull --preview fix-login
torque board sync pull --preview --group backend --json
```

Pull preview is operator-gated. The UI shows a local-vs-GitHub diff; select
fields and click **Apply selected**. CLI pull is preview/read-only.

## PR closing references

When GitHub board sync is enabled and **Close linked issues via PR body** is on,
`engineer_merge` on the PR path appends a `Linked Torque issues` section with
missing `Closes #123` or `Closes owner/repo#123` refs. Existing closing refs are
not duplicated. This requires `engineer_merge_mode=pr` or `engineer-choice`
without `force_direct=true`; direct-local merges have no PR body to update.

## Limits and recovery

- GitHub Issues + Projects v2 only; Projects classic is not supported.
- No webhooks or background polling in V1. Remote changes require manual pull
  preview; new external issues are not auto-imported without an operator gate.
- Failures surface as structured `type`/`provider`/`phase`/`error` data in UI,
  panel events, CLI JSON, and task `board_sync`.
- Common failures: missing `gh`, not logged in, missing `project` scope, repo or
  project not found, missing Status option, missing labels when label creation
  is disabled, permission errors, and rate limits. Fix, run **Test connection**,
  then retry sync.
