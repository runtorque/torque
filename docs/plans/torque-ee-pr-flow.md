# torque-ee PR flow — design spec + sequencing recommendation

**Task:** TORQUE:764 — Ship `ee/` (`runtorque/torque-ee`) changes via PRs instead of direct pushes to `torque-ee` `main`.
**Status:** Implemented in TORQUE:766; pending review/merge.
**Decision status:** Sequencing model is architect-confirmed/locked: **ee PR merges first; parent Torque PR merges second.**
**Approval path:** Human/architect approval is required before implementation because this changes merge sequencing, cross-repo release mechanics, and operational failure behavior.

**Implementation resolution note (TORQUE:766):** Section 13's open questions were
resolved before build. The implementation uses ee/nested PRs merged with merge
commits, keeps parent Torque PRs squash-merged, treats the ee PR as folded into
the existing review boundary, fails closed instead of direct-pushing ee main, and
does **not** depend on or enable branch protection.

---

## 1. Executive summary

This change is primarily a **merge-flow robustness win** and secondarily a **review-visibility win**.

Today, real commits under the `ee/` submodule can be landed by Torque's nested-submodule merge path directly onto `runtorque/torque-ee` `main`. The parent `runtorque/torque` PR only exposes the submodule as a gitlink/SHA bump, so reviewers do not get a normal `torque-ee` content diff. The same direct-push/publish neighborhood is also where the nested-ee false-failure catalog has accumulated, especially variant **#6 UNPUSHED / MISSING_FROM_REMOTE** and the manual `git -C ee push ...` fallback.

The decided model is:

1. A worker's `ee/` changes stay on the existing per-worktree nested branch in the submodule, e.g. `torque/submodules/ee/torque/<engineer>/<worker-branch>`.
2. Torque pushes that branch to `runtorque/torque-ee` and opens/reuses an **ee PR** there.
3. Torque merges the **ee PR first** into `torque-ee` `main`.
4. Torque syncs `ee` `main`, updates the parent superproject gitlink to the **merged ee-main SHA**, and records a mechanical parent gitlink bump.
5. Only then does Torque push/create/reuse/merge the parent `runtorque/torque` PR.

This preserves the important invariant: **the parent Torque PR is merged only when its `ee` gitlink points at a commit already on `torque-ee` `main`.** It also makes partial failures idempotent: if the ee PR lands but the parent merge fails later, the next `engineer_merge` detects that the gitlink already resolves to an ee-main commit and resumes at the parent merge step instead of creating or merging another ee PR.

A critical technical subdecision follows from existing boundary verification: **merge ee PRs with a merge commit, not squash/rebase.** The current mechanical gitlink reconciliation verifier requires the reviewed submodule commit to be an ancestor of the final ee-main commit. GitHub squash/rebase would create a new commit that is not descended from the reviewed ee branch head. A normal merge commit preserves that ancestry and matches today's direct nested merge shape.

---

## 2. Headline robustness impact: false-failure catalog shrink

This is the strongest reason to do the change. The new flow designs out the root of the worst nested-ee false-failure face by making the parent merge consume a commit that is already on `torque-ee` `main`, not a private/unpublished nested branch head.

| Catalog face | Current failure shape | New-flow impact | Why |
|---|---|---|---|
| #1 stale-base recheck after success | Post-success reinspection reports stale base even though PR landed. | **Unaffected by ee PR flow.** Should remain covered by the live authoritative post-success guard. | This is parent merge/finalization bookkeeping, not ee reachability. Keep verify-first/no-blind-retry guidance. |
| #2 conflicts detected after success | Post-success reinspection reports conflicts after merge/cleanup effects. | **Unaffected directly.** | Same parent merge post-success class. The ee flow must not add another post-success reinspection after cleanup. |
| #3 target-resolution tombstoned | Close/remove cleanup tombstones the agent, then a trailing tool pass tries to resolve it. | **Unaffected directly.** | Preserve the existing parent authoritative guard and metadata snapshot-before-cleanup behavior. Do not run ee PR checks after parent cleanup. |
| #4 `github_preflight` removed worktree | Worktree was removed after success, then a trailing GitHub preflight runs in the removed path. | **Unaffected directly, but the design avoids adding an ee variant.** | All ee PR work must happen before parent PR merge and before parent cleanup. Parent post-success false-failure handling remains the guard. |
| #5 `Agent not found` | Close/remove cleanup deletes/tombstones the target, then MCP target resolution runs again. | **Unaffected directly.** | Same target-resolution layer as #3. The new nested-ee phase should return success/pending before cleanup and never target-resolve after cleanup. |
| #6 nested-ee `UNPUSHED` / `MISSING_FROM_REMOTE` | Parent merge preflight requires the nested ee branch ref to be pushed/reachable; zero-delta or real-delta branches can fail even when the parent change is valid. | **Eliminated for the intended ee-change path; zero-delta path should also stop engaging this branch-push requirement.** | The ee branch is explicitly pushed as a PR head before any parent merge. The ee PR merges first. The parent gitlink is updated to the ee-main merge commit, so at parent time the gitlink is reachable from `origin/main`; no manual branch-push fallback is needed. |
| #6 adjacent `REMOTE_UNAVAILABLE` after cleanup in removed cwd | Merge actually succeeded, but a later nested cleanup/preflight step runs in a deleted worktree/admin dir. | **Not solved by this flow alone; adjacent cleanup-ordering bug.** | The ee PR flow reduces nested preflight/publish surface after parent success, but removed-cwd cleanup bugs still belong to the broader post-success guard / cleanup-ordering fix. Keep verify-first/no-blind-retry. |
| ee-fork-before-bump gitlink regression trap | Branch forked before an ee bump can regress parent `main`'s gitlink to an old ee SHA. | **Improved, with one required guard.** | The merge-first model updates the parent branch to the current merged ee-main SHA before parent merge. Implementation must compare against current `ee origin/main` and refuse/regenerate stale literal gitlinks. |
| Stray zero-delta ee drift on non-ee tasks | A broad `git add` captures an accidental ee gitlink bump and trips nested-submodule preflight. | **Should shrink if real-delta detection is strict.** | Common no-ee path must be `old_gitlink == new_gitlink` → no ee PR, no branch publish, no nested preflight. A real gitlink delta on a non-ee task remains a blocker to clean up. |

**Operator posture remains:** if `engineer_merge` reports a merge failure in this neighborhood, verify GitHub/main ground truth before retrying. The new flow should reduce false failures; it does not justify blind retries.

---

## 3. Locked sequencing and atomicity model

### 3.1 Decided model

**ee PR merges first → parent gitlink points at merged ee-main SHA → parent PR merges.**

This is not merely a preference; it is the model this spec designs around.

### 3.2 Why this model wins

- **Parent invariant:** parent `main` never lands a gitlink to a commit that only exists on a feature branch.
- **Reachability:** parent merge time can prove the gitlink is reachable from `torque-ee` `origin/main`, not merely from `origin/<feature-branch>`.
- **Idempotent partial failure:** if ee lands and parent does not, rerun resumes without duplicating ee work.
- **Review visibility:** `torque-ee` gets its own PR, URL, discussion, and content diff.
- **Existing verifier compatibility:** if the ee PR is merged with a merge commit, the reviewed ee branch head remains an ancestor of the merged ee-main SHA, so the existing nested gitlink reconciliation model remains valid.

### 3.3 Models rejected

- **Parent gitlink points at ee feature branch while both PRs merge concurrently:** rejected. It creates a window where parent references an off-main ee commit and reintroduces MISSING_FROM_REMOTE / branch-lifecycle fragility.
- **Parent PR merges first, ee later:** rejected. Parent `main` would reference a not-yet-main ee commit and can break checkout/release reproducibility.
- **Continue direct-pushing `ee main` but also create a PR:** rejected. It keeps the review gap and the current false-failure/manual-push failure surface.

---

## 4. End-to-end flow

### 4.1 Common case: no ee delta

Most Torque PRs do not touch `ee/`.

1. `engineer_merge` syncs parent base as it does today.
2. It detects `ee` is a configured nested submodule but has **zero real delta**:
   - parent base gitlink SHA == worker branch gitlink SHA, and
   - submodule HEAD matches that gitlink or is detached at that same SHA, and
   - the gitlink commit is already reachable from `ee origin/main`.
3. It does **not** push the nested ee branch.
4. It does **not** create an ee PR.
5. The parent PR flow proceeds unchanged.

This is a hard requirement: the common no-ee path must stay simple and must not regress into the old zero-delta UNPUSHED behavior.

### 4.2 Real ee delta

1. Parent merge preflight runs the normal local safety gates first: clean worktree, branch boundary, sibling divergence, stale base, superproject conflicts/overwrite.
2. Torque detects a real `ee` delta:
   - base gitlink != branch gitlink, and
   - submodule HEAD == branch gitlink, and
   - the submodule is on a named nested branch, not detached, unless the commit is already on ee `main`.
3. Torque pushes the nested ee branch to `runtorque/torque-ee`.
4. Torque creates or reuses a `torque-ee` PR from that branch to `main`.
5. Torque requests a **merge-commit merge** of the ee PR, guarded by the expected ee branch head SHA.
6. If GitHub reports checks/reviews pending, Torque returns a non-cleanup `pending` result with the ee PR URL and records ee PR metadata on the worktree boundary. The parent PR is not merged and preferably is not created yet.
7. Once the ee PR is merged, Torque syncs the local `ee main` to the ee PR merge commit.
8. Torque resets the worker's `ee` nested worktree to that merged ee-main commit.
9. Torque stages the parent `ee` gitlink and creates a mechanical superproject commit, e.g. `Update ee submodule to torque-ee PR #NN`.
10. Torque reruns the cheap parent conflict/overwrite checks against the final parent branch tip.
11. Torque pushes the parent branch, creates/reuses the parent Torque PR, and merges it through the existing parent PR path.
12. Parent cleanup runs only after the parent PR merge is confirmed.

### 4.3 Existing parent PRs

If a parent PR already exists before the ee PR lands, `engineer_merge` must update the parent branch after the ee PR merge so the parent PR's final gitlink points at the merged ee-main SHA. It must not merge a parent PR whose head still points at the ee feature-branch commit.

Recommended V1 simplification: for branches with a real ee delta, `engineer_create_pr` should create/reuse the ee PR and return `pending_ee_pr` without creating the parent PR until the ee PR is merged, unless the parent PR already exists. This preserves the invariant most cleanly.

---

## 5. Failure-mode and idempotency analysis

### 5.1 ee PR create fails

- Return `ok: false`, `phase: nested_submodule_pr_create`, with the attempted branch, repo, and URL/selector if known.
- No parent PR is created/merged.
- No cleanup runs.
- Retry is safe after transient GitHub/network failures because the branch push is deterministic and `github_create_or_reuse_pr` reuses an existing open PR.

### 5.2 ee PR merge blocked by checks/reviews

- Return `ok: true`, `pending: true`, `merged: false`, `phase: nested_submodule_pr_merge`, with `ee_pr.url` and `pending_submodule_pr: true`.
- Record ee PR metadata on the latest open boundary.
- Do not merge parent.
- Do not cleanup worker/worktree.
- Rerun `engineer_merge` after the folded review/checks pass; it reuses the ee PR and tries the guarded merge again.

### 5.3 ee PR merge conflict

- Return a blocking merge error with the ee PR URL and `merge_state` / conflict details available from GitHub.
- Parent PR is not merged.
- Resolution is the existing rebase/fix cycle, but it must cover the nested ee branch: rebase the parent worktree and nested ee branch onto current bases, then rerun review if the actual content changes.

### 5.4 ee PR merged, parent merge fails afterward

This is the key partial-failure/idempotency case.

On the next `engineer_merge`:

1. Fetch/sync `ee origin/main`.
2. Detect the ee branch's reviewed commit is already incorporated into ee `main` via the previously merged ee PR.
3. If the parent gitlink already points at that ee-main commit, skip ee PR work entirely.
4. If the parent gitlink still points at the old feature-branch commit, create the mechanical parent gitlink bump to the merged ee-main commit and proceed.
5. Continue with parent branch push/create/reuse/merge.

No duplicate ee PR, no second ee merge, no direct push.

### 5.5 ee PR open but not merged on retry

- Reuse the open PR by head branch.
- Recheck that the local ee branch head matches the PR head SHA.
- If local changed, push the updated branch with normal safe-force semantics only if the remote head is an ancestor or already incorporated; otherwise fail with a clear non-fast-forward/safety message.
- Attempt merge again or return pending.

### 5.6 Concurrent ee merges

Two parent branches can both carry ee changes.

- First ee PR lands and advances `ee main`.
- Second ee PR merge may become blocked/conflicted by the new base.
- The second parent merge must not proceed until its ee PR has merged into current ee `main`.
- If the second branch's ee change is additive and GitHub can merge it, the final parent gitlink points at the second ee-main merge commit.
- If not, the engineer must rebase/fix the ee branch; no direct push fallback.

### 5.7 Parent PR already merged but Torque reports failure

Unchanged verify-first rule:

- Confirm parent PR is `MERGED` and parent `origin/main` is at the merge commit.
- Confirm parent gitlink points at an ee commit reachable from `torque-ee origin/main`.
- If both are true, treat the merge as shipped and record post-success warning, not as a retry signal.

---

## 6. Engineer merge integration

### 6.1 Recommendation: automatic, not an explicit operator step

The default `engineer_merge` path should own the ee PR sequence automatically. Requiring engineers to remember a separate `publish-ee`, `open-ee-pr`, or manual `git -C ee push` step would recreate the current failure-prone workflow.

The operator-facing contract stays:

```text
engineer_merge(agent=..., pr_title=..., pr_body=...)
```

When a real ee delta exists, the returned payload includes a nested ee section, for example:

```json
{
  "nested_submodules": {
    "ok": true,
    "phase": "nested_submodule_pr_merge",
    "submodules": [{
      "path": "ee",
      "pr": {"url": "https://github.com/runtorque/torque-ee/pull/NN", "state": "MERGED"},
      "reviewed_sha": "...",
      "merged_main_sha": "...",
      "parent_gitlink_bump_sha": "..."
    }]
  }
}
```

### 6.2 Placement in `_run_pr_worktree_merge`

Current code shape in `torque/server.py`:

1. GitHub preflight/select remote.
2. Sync parent remote base.
3. `_preflight_worktree_merge_gates(... publish_nested_submodule_branches=True)`.
4. `_merge_nested_submodules_for_merge(...)` directly integrates/pushes nested submodule base.
5. Push parent branch, create/reuse parent PR, merge parent PR.

New shape:

1. GitHub preflight/select remote for parent.
2. Sync parent remote base.
3. Run local parent safety gates without zero-delta nested branch publication side effects.
4. Call a new nested-ee PR orchestration helper for real ee deltas.
5. If helper returns pending/error, return before parent PR create/merge and before cleanup.
6. If helper merged/skipped, rerun parent post-nested conflict/overwrite checks.
7. Push/create/reuse/merge parent PR as today.

### 6.3 Direct parent merge fallback

`force_direct=true` may still be useful for the parent repo in emergencies, but it must **not** resurrect direct pushes to `torque-ee main`.

Recommended behavior:

- If no ee delta: direct parent fallback stays unchanged.
- If real ee delta: first run the same ee PR merge-first sequence; after the parent gitlink points at merged ee-main, the parent repo may use direct local merge if the group setting/operator explicitly allows it.
- If GitHub/gh auth is unavailable for `torque-ee`, fail closed for ee changes. Do not silently push `ee main`.

---

## 7. Evolution of :748 nested-ee auto-publish

### 7.1 What remains

- The per-worktree nested ee branch model remains: `torque/submodules/ee/<parent-worker-branch>`.
- Branch push remains, but its purpose changes: it publishes the PR head for review/merge, not merely a reachability hack for parent preflight.
- Existing dirty/HEAD-mismatch checks remain valuable.

### 7.2 What retires

- Direct `ee main` push from `_merge_nested_submodules_for_merge` for `ee` changes.
- The operator/manual fallback that pushes the nested ee branch just to satisfy UNPUSHED preflight.
- Zero-delta branch publication as a merge requirement.

### 7.3 Compatibility with non-ee submodules

V1 should be explicit: this work is for `ee/` → `runtorque/torque-ee`. If the implementation generalizes helper names, keep behavior gated by configured submodule path and GitHub remote support. Do not accidentally change arbitrary third-party submodule merge behavior without a separate design decision.

---

## 8. Review composition

### 8.1 Recommendation: folded into the existing engineer review boundary

The ee PR exists for:

- independent `torque-ee` content-diff visibility,
- a proper `torque-ee` PR discussion/audit URL,
- clean `torque-ee` history,
- robust parent gitlink reachability.

It should **not** create a second human-review cycle by default. The same engineer/reviewer who reviews the parent Torque change reviews the ee content as part of one logical change.

### 8.2 How reviewers should reason about it

- If a task touches only parent Torque files, nothing changes.
- If a task touches `ee/`, the review boundary must explicitly include the ee content delta, either from local nested diff tooling or the ee PR URL.
- Once that folded review says "Ship", `engineer_merge` may create/reuse and merge the ee PR automatically before the parent PR.
- If `torque-ee` branch protection requires a GitHub review, the same folded reviewer/engineer can satisfy it; this is a repository policy mechanism, not a second product workflow.

### 8.3 Avoiding double-review thrash

A mechanical parent gitlink bump after the ee PR merge must not force a new full review cycle. It is valid only if machine verification proves:

- the parent post-review commits modify only configured submodule gitlink paths,
- the new gitlink is the ee PR merge commit on `ee origin/main`,
- the reviewed ee branch head is an ancestor of that merge commit,
- the base ee commit is also incorporated, and
- the resulting tree is the clean merge of reviewed ee content plus current ee base.

That is why ee PRs should use merge commits, not squash/rebase.

---

## 9. Backward compatibility

- Existing `torque-ee main` history stays valid, including commits that landed via direct push before this change.
- Parent branches whose `ee` gitlink already points at a commit reachable from `ee origin/main` are treated as already published/merged for ee purposes.
- Grandfathered direct-pushed commits are not rewritten and do not need PR backfill.
- If a historical branch carries an ee feature SHA that was manually/directly landed to `ee main`, the new flow should detect reachability and skip duplicate ee PR creation.
- If a branch carries an ee commit that is not reachable from `ee main` and cannot be pushed as a PR head, the new flow fails closed with a clear repair path.

---

## 10. Implementation plan

### Phase 0 — approval

- Review this spec with Courier/Torqly/human owner.
- Confirm open questions in section 13, especially ee PR merge strategy and `engineer_create_pr` behavior.
- No implementation before approval.

### Phase 1 — WorktreeManager primitives

Files:

- `torque/worktree.py`
- `tests/test_worktree_submodules.py`

Add/adjust primitives:

1. Detect nested submodule delta state for `ee`:
   - zero delta,
   - real delta needing PR,
   - already-on-ee-main,
   - dirty/head mismatch/detached invalid states.
2. Push nested ee branch as a PR head.
3. Create/reuse a PR in the submodule repo using existing GitHub helper patterns, but with submodule cwd.
4. Add a merge-commit PR merge helper (`gh pr merge --merge --match-head-commit`) or strategy parameter; keep parent PR squash behavior unchanged.
5. Confirm ee PR merged and `ee main` synced to the merge commit.
6. Update/reset the nested ee worktree and expose the merged ee-main SHA for the parent gitlink bump.
7. Delete the remote ee feature branch only as optional cleanup; correctness must not depend on deletion.

### Phase 2 — Server merge integration

Files:

- `torque/server.py`
- `torque/worktree.py`
- `tests/test_server_self_dispatch.py`
- `tests/test_worktree_submodules.py`

Changes:

1. Replace the `ee` path through `_merge_nested_submodules_for_merge` with the new ee PR orchestration helper.
2. Keep local parent preflight before ee side effects.
3. Return `pending` cleanly when the ee PR is open/blocked; do not create/merge parent PR and do not cleanup.
4. After ee PR merge, commit the parent gitlink bump and rerun parent post-nested checks.
5. Preserve existing authoritative parent PR success guard and metadata snapshot-before-cleanup.
6. Record ee PR metadata on the latest open boundary under a nested/submodule-specific key.
7. Ensure direct parent fallback still runs ee PR first for real ee deltas.

### Phase 3 — Create-PR/review visibility integration

Files:

- `torque/worktree.py`
- `torque/server.py`
- `torque/mcp_tools_shared.py`
- `torque/mcp_engineer_tools/tool_specs.py`
- `docs/reference/mcp-tools.md`
- `docs/tasks/worktrees.md`
- `docs/team/engineers.md`

Changes:

1. Update `engineer_create_pr` / `worktree_create_pr` for ee deltas:
   - create/reuse ee PR and return its URL,
   - do not create a mergeable parent PR that points at an unmerged ee feature SHA,
   - if parent PR already exists, mark/report dependency and ensure final merge updates it after ee lands.
2. Update tool descriptions so engineers understand that ee PR review is folded into the existing review boundary.
3. Remove manual `git -C ee push` fallback guidance from docs; replace with "rerun engineer_merge after verifying/pending ee PR" guidance.

### Phase 4 — Reliability/idempotency hardening

Files:

- `torque/mcp_retry.py`
- `torque/mcp_tools_shared.py`
- `tests/test_mcp_reliability.py`
- `tests/test_server_self_dispatch.py`

Changes:

1. Classify new phases (`nested_submodule_pr_create`, `nested_submodule_pr_merge`, `nested_submodule_pr_sync`, `nested_submodule_gitlink_bump`) for retry/idempotency.
2. Cache only non-retryable failures; transient GitHub/network phases should not poison retries.
3. Ensure post-success warnings stay warnings if either ee or parent PR is authoritatively confirmed merged.
4. Preserve verify-first/no-blind-retry messaging in formatted MCP errors.

### Phase 5 — Documentation cleanup

Files:

- `docs/tasks/worktrees.md`
- `docs/team/engineers.md`
- `docs/reference/mcp-tools.md`
- Possibly `docs/operate/manual-testing.md`

Changes:

1. Document the ee PR merge-first model.
2. Document zero-delta behavior.
3. Document pending ee PR behavior and rerun semantics.
4. Document folded review composition.
5. Retire direct-push/manual branch-push fallback guidance.

---

## 11. Test strategy

### Unit/integration tests with local bare repos

Add to `tests/test_worktree_submodules.py`:

1. **zero-delta does nothing**
   - Worker has configured `ee` submodule but no ee gitlink delta.
   - Assert no nested branch push, no ee PR helper call, parent path continues.

2. **real ee delta opens and merge-commits ee PR first**
   - Local super repo + bare submodule origin.
   - Worker changes ee, checkpoints parent gitlink.
   - Fake or local-GitHub-helper shim reports ee PR merge commit.
   - Assert parent gitlink after helper equals ee `origin/main` merge commit.
   - Assert reviewed ee commit is ancestor of merged ee-main commit.

3. **parent PR sees merged ee-main SHA**
   - In `_run_pr_worktree_merge`, capture gitlink at parent `github_push_branch` time.
   - Assert it equals ee `origin/main`, not the pre-merge feature branch SHA.

4. **ee PR pending blocks parent**
   - Helper returns pending.
   - Assert parent `github_push_branch`, parent `github_create_or_reuse_pr`, parent merge, cleanup are not called.
   - Boundary records ee PR metadata.

5. **partial failure idempotency: ee merged, parent failed**
   - First run: ee helper merges, parent merge returns failure.
   - Second run: helper detects already merged/reachable ee commit and does not create a second ee PR; parent proceeds.

6. **ee PR open on retry**
   - Existing open PR by branch is reused.
   - If local head unchanged, no duplicate PR.
   - If local head changed and safe-force is refused, return clear non-FF error.

7. **ee PR conflict**
   - PR merge helper reports conflict/dirty merge state.
   - Assert parent not touched and error includes ee PR URL.

8. **direct parent fallback still uses ee PR first**
   - `force_direct=true` with ee delta.
   - Assert ee PR helper runs before parent direct merge; no direct push to ee main.

9. **merge strategy guard**
   - Simulate ee PR squash merge where reviewed SHA is not ancestor of merged SHA.
   - Assert mechanical gitlink reconciliation refuses or returns an explicit unsupported-merge-strategy error.

10. **ee-fork-before-bump regression prevention**
    - Branch forked before ee main advanced.
    - Assert final parent gitlink is current ee main after ee PR/reconciliation, not the stale branch literal gitlink.

### Server/MCP tests

Add to `tests/test_server_self_dispatch.py` and `tests/test_mcp_reliability.py`:

1. Call-order test for parent PR path with ee delta.
2. Pending ee PR returns `ok: true, pending: true`, not `isError`, and does not cleanup.
3. `nested_submodule_pr_create`/`merge` retryable phase behavior.
4. Boundary metadata includes parent PR and ee PR distinctly.
5. Post-success cleanup false-failure regression: after parent merge success and cleanup removes worktree, no nested ee preflight runs in removed cwd.
6. Existing `already_merged` / `No commits between` parent PR false-failure guard still works with nested ee metadata attached.

### Manual smoke tests (non-worker shell only)

1. Create a disposable parent repo with `ee` as a local/bare GitHub-like submodule remote.
2. Run a worker branch with no ee delta and verify no nested branch push is required.
3. Run an ee delta through a fake/stubbed GitHub PR path or a test GitHub repo if available.
4. Force the partial-failure point after ee PR merge but before parent PR merge; rerun and verify idempotent resume.
5. Confirm docs no longer instruct manual `git -C ee push` as the normal fallback.

Do **not** run `make deploy`, `make stop`, or lifecycle commands from a worker worktree.

---

## 12. Files likely to change

Implementation should be expected to touch:

- `torque/worktree.py`
  - nested submodule delta detection,
  - submodule PR create/reuse/merge helpers,
  - merge-commit PR strategy,
  - direct-push retirement for ee.
- `torque/server.py`
  - `_run_pr_worktree_merge`, `_run_direct_worktree_merge`, `_preflight_worktree_merge_gates`, boundary metadata recording.
- `torque/mcp_tools_shared.py`
  - formatted merge result/error payloads, pending ee PR messaging.
- `torque/mcp_engineer_tools/tool_specs.py`
  - `engineer_merge` / `engineer_create_pr` descriptions.
- `torque/mcp_retry.py`
  - retry classification for new phases.
- `docs/reference/mcp-tools.md`, `docs/tasks/worktrees.md`, `docs/team/engineers.md`
  - operator documentation.
- Tests:
  - `tests/test_worktree_submodules.py`,
  - `tests/test_server_self_dispatch.py`,
  - `tests/test_mcp_reliability.py`,
  - possibly focused docs/tool-schema tests if existing coverage expects exact descriptions.

---

## 13. Open questions for approval

1. **Confirm ee PR merge strategy:** I recommend **merge commit only** for ee PRs. Squash/rebase is technically problematic with the existing mechanical gitlink verifier because the reviewed ee branch head stops being an ancestor of the final ee-main SHA.
2. **Confirm `engineer_create_pr` behavior for ee deltas:** Recommended V1 behavior is create/reuse ee PR and return pending/dependency metadata, but avoid creating a mergeable parent PR until the ee PR has merged.
3. **Remote branch cleanup:** Should Torque delete the remote nested ee feature branch after ee PR merge, or leave it for audit/debugging and rely on later pruning? Correctness should not depend on deletion.
4. **Scope:** Is V1 intentionally `ee/`-specific, or should the helper be built generic for all configured GitHub submodules but enabled only for `ee`? I recommend ee-specific behavior gate with generic internal naming only where it reduces duplication.
5. **Branch protection:** Should `torque-ee` enforce a GitHub review/check requirement, or is the folded Torque engineer review boundary sufficient and GitHub branch protection optional? The spec assumes folded review is sufficient by product workflow.
6. **Emergency override:** Is there any acceptable hidden/human-only override for direct-pushing `ee main`, or should all ee deltas fail closed if the ee PR path is unavailable? I recommend fail closed; manual direct push remains an out-of-band recovery, not a Torque feature.

---

## 14. Technical concern flagged

The only technically problematic part of the locked model is not the sequencing; it is the **ee PR merge method**. If the implementation reuses the existing parent helper (`github_request_squash_merge`) for the ee PR, the parent gitlink bump will point at a squash commit that does not descend from the reviewed ee commit. That conflicts with the current `gitlink_reconciliation_boundary_state` invariant and can either force unnecessary re-review or require a looser verifier.

The clean fix is to add a merge-commit PR merge path for ee PRs and keep parent Torque PRs squash-merged as they are today.
