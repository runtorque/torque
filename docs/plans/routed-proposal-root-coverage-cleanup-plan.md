# Routed PM-root coverage cleanup plan

Status: TORQUE:1020 review amendment, created after TORQUE:1021 found the original handoff missing an explicit stale-root cleanup plan.

This is a **reviewable plan only**. Do not perform any cleanup before the TORQUE:1020 authorization fix merges, deploys, and the operator intentionally runs each covered-root action.

## Safety rules

- Actor: Torqly (`a5a7fc9e`) should run cleanup from an Architect context after the authorization fix is deployed/relaunched.
- Operation: use only `architect_task_mark_covered` with `move_to_done=true` against one product-proposal root at a time.
- Required target predicate before every call:
  - root task is visible in the Torque group;
  - root is labeled `product-proposal` and `proposal-only`;
  - root has durable routing/coverage evidence from Blueprint/PM to Torqly, or a Torqly-created covering task with a `covers:<ROOT>` label;
  - covering evidence includes covering task, PR URL, merge SHA, tests/review evidence, and notes.
- Never bulk-close roots silently.
- Do not dispatch new work from cleanup.
- Do not use destructive task edits, delete, reassign, or arbitrary lane moves as a substitute for covered/done evidence.
- Do not grant Product Manager dispatch or mutation authority.
- Preserve root labels/history; only append coverage evidence and move to Done through the scoped tool.
- If a root lacks sufficient covering evidence, leave it open/backlog and add a visible note rather than forcing closure.

## Cleanup order

1. Verify each root still exists and still has `product-proposal` / `proposal-only` labels.
2. Process roots with merged implementation evidence first: TORQUE:999, TORQUE:1007, TORQUE:1015.
3. Process repeat/superseded roots only after confirming the later covering task intentionally covers the earlier root: TORQUE:991, TORQUE:997, TORQUE:1001, TORQUE:1009.
4. Record one `architect_task_mark_covered` call per root. Include the root-specific evidence below in the `notes`/`tests` fields.
5. After all successful calls, run a read-only board check to confirm each intended root moved to Done and still carries its product labels and coverage evidence.

## Per-root plan

| Root | Current reason it is stale | Covering evidence source | Cleanup disposition |
| --- | --- | --- | --- |
| `TORQUE:991` — Ask Help still no-op after Help regression fix | Early PM Help post-smoke root. Later Help Ask roots showed PR #797/#806 were insufficient until the right-rail visibility fix. | Primary covering stream: `TORQUE:1008` (`covers:TORQUE:1007`) / manual review `TORQUE:1014` / PR #816 `https://github.com/runtorque/torque/pull/816`, reviewed head `c78a00814bbeaf1190af924f52844a9bc3795e86`, squash `fcc588542c7c692a698d9f82648b923ee10c14e7`. Supporting earlier attempts: PR #797 `25afd7035db79a4fcba5d1fe6b22e3be62f0b231` and PR #806 `4e3bf52ba81f6e210ef85365794da08611a9efc2` are historical but not sufficient alone. | Eligible only if Torqly confirms the final right-rail Help fix supersedes the earlier Ask no-op root. Cleanup note should say the root was superseded by the repeat-failure fix in `TORQUE:1008` / PR #816 and not by the failed intermediate fixes alone. |
| `TORQUE:997` — Ask Help still broken after PR #797 smoke | Repeat PM Help root after PR #797. | Same final covering stream as above: `TORQUE:1008` / `TORQUE:1014` / PR #816 `https://github.com/runtorque/torque/pull/816`, reviewed head `c78a00814bbeaf1190af924f52844a9bc3795e86`, squash `fcc588542c7c692a698d9f82648b923ee10c14e7`. Supporting failed intermediate: PR #806 `4e3bf52ba81f6e210ef85365794da08611a9efc2`. | Eligible if Torqly confirms PR #816's root cause (“PR #806 proved DOM/API but live right rail cropped Ask result states”) covers this repeat root. Use one covered call with `covering_task=TORQUE:1008`, PR #816 URL/SHA, Help tests and review evidence from TORQUE:1008. |
| `TORQUE:999` — Deep DM composer / terminal coupling investigation | proposal root explicitly routed into the deep DM composer stream. | Covering stream: `TORQUE:1000` (`covers:TORQUE:999`) / manual review `TORQUE:1006` / PR #809 `https://github.com/runtorque/torque/pull/809`, reviewed clean tip `1dc3af9c92edf57caafa074ede7ce1fcd2ac519e`, squash `157f15baf115e8239e7b5e7305b0850d4574bee3`. Tests: `node --test tests/frontend_state_regression.test.js` (626 passed), focused Python frontend tests, review Ship/no blockers. | Eligible. Use `covering_task=TORQUE:1000`, move to Done, notes: terminal/DM composer coupling fixed; PR #770/#781/#782/#792 semantics preserved; no backend/PTY changes. |
> Note: TORQUE:1115 removed the Codex SDK runner/prototype; the Codex SDK rows below are historical closure evidence only and must not be treated as an available runtime or smoke recipe.

| `TORQUE:1001` — Codex SDK smoke add_agent runner_backend argument | Initial PM SDK smoke root; later repeat root `TORQUE:1009` proved live failure after PR #807. | Covering implementation for the code path: PR #807 `https://github.com/runtorque/torque/pull/807`, reviewed head `6d6d125c8bf6f62a9cbd31ff12fd76c19e84bfc4`, squash `07c53f80dd1c5e8219e1fc75bde21f00f26320e0`, plus `TORQUE:1012` manual review (`covers:TORQUE:1009`) confirming clean origin/current-source was not the remaining bug and the live failure was dirty/staged root + stale installed app source. | Conditional. Only mark covered if Torqly explicitly treats PR #807 + TORQUE:1012 as sufficient for the original root. If cleanup wants stricter coverage, leave `TORQUE:1001` open until a visible covering task is labeled `covers:TORQUE:1001` or a post-deploy smoke confirms the exact original command no longer fails. |
| `TORQUE:1007` — Ask Help still broken after PR #806 live smoke | Repeat Help live-smoke escalation explicitly routed to Torqly. | Covering stream: `TORQUE:1008` (`covers:TORQUE:1007`) / manual review `TORQUE:1014` / PR #816 `https://github.com/runtorque/torque/pull/816`, reviewed head `c78a00814bbeaf1190af924f52844a9bc3795e86`, squash `fcc588542c7c692a698d9f82648b923ee10c14e7`. Tests: frontend Help and state regression suites; focused Help docs/MCP tests; review Ship/no blockers. | Eligible. Use `covering_task=TORQUE:1008`, move to Done, notes: right-rail Help result visibility root cause fixed while preserving read-only Help. |
| `TORQUE:1009` — Codex SDK add_agent smoke still hits runner_backend error after PR #807 | Repeat PM SDK smoke root after PR #807. | Covering review/diagnosis: `TORQUE:1012` (`covers:TORQUE:1009`, reviews `TORQUE:1010`) confirmed the failure was not a current-origin SDK runner bug: live daemon/import path used dirty/staged root source and installed app was stale; clean origin `157f15baf115e8239e7b5e7305b0850d4574bee3` accepted `runner_backend` and focused guardrail suites passed. | Eligible as a diagnosis/known-operator-action closure only if Torqly accepts source-mismatch diagnosis as coverage. Use `covering_task=TORQUE:1012`, move to Done, notes must state “operator source cleanup/reset + normal non-worker deploy/relaunch + exact smoke rerun still required”; do not claim a code fix was merged for this root. |
| `TORQUE:1015` — Codex SDK smoke fails on Sandbox.read_only hyphenated enum value | PM SDK smoke root for installed SDK enum compatibility. | Covering implementation: PR #821 `https://github.com/runtorque/torque/pull/821`, reviewed head `9d57194c69de13c9ee978601256506256a63eb18`, squash `4eef6795fe3e87171777faf4f4fcc7fbe4aad14c`. The PR title is “Accept hyphenated Codex SDK read-only sandbox,” matching the root symptom. | Eligible after verifying PR #821's task/review chain is accepted in Torque or by Torqly. Use PR #821 URL/SHA/tests/review notes; if no visible Torque covering task is available, include a note that this root is closed from PR-level evidence rather than a `covers:<ROOT>` task label. |

## Template per cleanup call

Use this structure for each root; fill in only root-specific evidence above:

```text
architect_task_mark_covered(
  task="TORQUE:<root>",
  covering_task="TORQUE:<covering-task-if-visible>",
  move_to_done=true,
  pr_url="https://github.com/runtorque/torque/pull/<pr>",
  sha="<merge-sha-or-reviewed-sha>",
  tests="<focused/full/review evidence from covering stream>",
  notes="Post-merge cleanup for Blueprint/product proposal root. Covered by <covering task/PR>; no bulk mutation; labels/history preserved; no dispatch/destructive edit. <operator caveat if any>."
)
```

For roots with only PR-level evidence and no visible `covering_task`, do **not** guess a covering task id. Either leave the root open or have Torqly add an explicit note/covering label through a reviewed path before calling `architect_task_mark_covered`.

## Review checklist for this amendment

- The plan lists all requested roots: `TORQUE:991`, `TORQUE:997`, `TORQUE:999`, `TORQUE:1001`, `TORQUE:1007`, `TORQUE:1009`, `TORQUE:1015`.
- The plan separates eligible roots from conditional roots.
- The plan does not authorize bulk closure, dispatch, destructive edits, or PM authority expansion.
- The plan requires per-root visible evidence and one scoped mark-covered call per root after deploy/relaunch.
- The plan preserves labels/history and audit evidence.
