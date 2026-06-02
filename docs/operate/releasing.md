# Releasing Torque

Releases are cut by the **`Release macOS`** GitHub Actions workflow
(`.github/workflows/release-macos.yml`). The workflow owns version handling
end-to-end — you no longer hand-edit version files or push tags. Triggering it
with a version is the entire release gesture.

## Cut a release

```bash
gh workflow run release-macos.yml -f version=2.2.0
```

That single dispatch runs the whole pipeline:

1. **prepare** — stamps the version into every source via
   `scripts/set_release_version.py`, commits `Bump version to 2.2.0` to `main`,
   and creates the **annotated** tag `v2.2.0` *on that commit* (the tag always
   points at the fully-bumped tree).
2. **build** — checks out `v2.2.0` and builds the signed macOS desktop bundle
   for Apple Silicon and Intel.
3. **release** — publishes the GitHub Release `v2.2.0` with both-arch
   `.dmg` / `.app.zip` assets plus `SHA256SUMS`.

`version` is the bare semver (`2.2.0`, no leading `v`). A real cut refuses to
run if the tag already exists — bump to a new version instead.

## Validate safely first (dry run)

Before a real cut, or when changing the release pipeline, validate end-to-end
without touching `main`:

```bash
gh workflow run release-macos.yml -f version=2.2.0 -f dry_run=true
```

A dry run bumps + commits to a **throwaway branch** (`ci/release-dryrun/…`),
tags it with a throwaway tag, builds both arches, publishes a **draft
prerelease**, then **deletes** the draft release, throwaway tag, and branch.
`main` is never modified. Watch it with `gh run watch <run-id>`.

## What carries the version

`scripts/set_release_version.py X.Y.Z` is the single source of truth for the
five version sources and rewrites each deterministically:

| File | Read by | When |
| --- | --- | --- |
| `VERSION` | `torque` daemon (`server._read_torque_version`) | runtime |
| `torque/__init__.py` `__version__` | `mcp.SERVER_INFO`, db migration major-gate | runtime |
| `src-tauri/Cargo.toml` (`torque-desktop` package) | `cargo tauri build` | build |
| `src-tauri/tauri.conf.json` | Tauri bundle version | build |
| `src-tauri/Cargo.lock` (`torque-desktop` self-entry) | `cargo` lockfile consistency | build |

Run it locally to inspect or verify:

```bash
python3 scripts/set_release_version.py 2.2.0          # stamp all five
python3 scripts/set_release_version.py 2.2.0 --check  # verify, no writes
```

## Notes

- **No assert-or-fail gate.** The old workflow asserted the tag against the
  version files and red-failed a release on any mismatch — that is gone. The
  workflow writes the files itself, so they are consistent by construction.
- **There is no `push: tags` trigger.** Pushing a `v*` tag by hand does **not**
  start a release; dispatch the workflow instead. (This also removes any
  self-retrigger loop from the workflow creating its own tag.)
- **`main` must allow the Actions bot to push.** `prepare` commits the bump and
  tag to `main` using the built-in `GITHUB_TOKEN` (`permissions: contents:
  write`). `main` is currently unprotected. If `main` is later protected, allow
  `github-actions[bot]` to push, or supply a token/app with that permission.
- **Signing / notarization** are unchanged: they activate when the
  `APPLE_CERTIFICATE` / `APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID` secrets
  are configured, and fall back to ad-hoc signing without notarization
  otherwise.
