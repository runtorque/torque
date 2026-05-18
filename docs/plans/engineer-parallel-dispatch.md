# Engineer parallel dispatch research plan

## Summary

Engineers are **underusing `engineer_batch_dispatch` as a tool surface**, but the data does **not** reduce cleanly to "engineers are doing work serially." In several recent cases, engineers used multiple `engineer_task_dispatch` calls seconds apart, and the workers then ran in parallel. That still misses the intended batch affordance, but its throughput cost is small for 2-3 tasks.

The actionable problem is narrower:

1. The engineer prompt and MCP tool description frame `engineer_batch_dispatch` mostly as an **ordered / capped / same-agent queue** tool, not as the default way to launch an independent parallel wave.
2. Engineers have learned real batch-related failure modes (`max_concurrent` ambiguity, cap-stuck retries, deferred queue / handoff DOA), so avoiding batch is sometimes rational.
3. `engineer_task_dispatch` is more explicit and expressive for one-off control (per-task provider/model/name, existing-agent recovery, manual checkpointing), so engineers reach for it even when a simple batch would work.

Recommendation: **fix the batch UX/reliability gaps first or alongside prompt changes**, then add a concise decision tree to the engineer prompt and make the batch tool description explicitly say it is the preferred dispatch surface for N>1 ready independent tasks.

## Methodology

Data sources audited:

- Runtime DB: `~/.torque/torque.db`
  - `mcp_idempotency` for actual `engineer_task_dispatch` / `engineer_batch_dispatch` calls and timestamps.
  - `board_tasks` / `engineer_task_log` / `agents` to map task IDs to engineers and waves.
  - `panel_events` and `engineer_journal` for dispatch envelopes, rationale, and failure notes.
- Code:
  - `torque/engineer.py` — actual engineer system prompt builder and policy text.
  - `torque/mcp_engineer_tools/tool_specs.py` — engineer tool schemas/descriptions.
  - `torque/mcp_tools_shared.py` — batch dispatch implementation.
  - `torque/state.py`, `torque/server_dispatch.py` — auto-dispatch queue state and pumping.
  - Existing tests around prompt, MCP tool schemas, and queue behavior.

Notes on interpretation:

- `mcp_idempotency.response_json` records results, not full request arguments. I inferred engineer ownership from `board_tasks.assigned_engineer_id` and worker ownership.
- A row of several `engineer_task_dispatch` calls within seconds is **serial tool use**, but not necessarily **serial worker execution**.
- "Independent / parallel-safe" was classified from task descriptions, architect envelopes, and engineer journals, not purely from timestamps.

## Quantitative data

### Aggregate MCP usage

From `mcp_idempotency` for `surface='engineer'`:

| Scope | `engineer_task_dispatch` calls | `engineer_batch_dispatch` calls | Task entries covered by batch | Notes |
|---|---:|---:|---:|---|
| All recorded since 2026-04-23 | 266 | 17 | 53 | Batch is only 6% of dispatch calls, 17% of task entries. |
| Courier | 85 | 1 | 2 | Courier almost never uses batch, including recent independent research waves. |
| Panelsmith | 101 | 12 | 38 | Panelsmith uses batch for planned UI waves, then often uses task dispatch for recovery/follow-ups. |
| Unknown / older migrated engineers | 75 | 4 | 13 | Includes older Slack/Atlas-era data with incomplete owner mapping. |

Batch result statuses across all 53 batch task entries:

| Status | Count | Meaning |
|---|---:|---|
| `dispatched` | 32 | Worker started immediately. |
| `queued` | 11 | Same-agent queued behind an active task. |
| `deferred` | 7 | Held by `max_concurrent` auto-dispatch queue. |
| `failed` | 3 | Already queued / dependency / validation failures. |

### Recent multi-task waves audited

Times below are UTC from the Torque DB.

| # | Time | Engineer | Tasks / shape | Tool pattern | Classification |
|---:|---|---|---|---|---|
| 1 | 2026-05-18 23:10 | Courier | `:447`, `:445`, `:443` — 3 independent research streams | `engineer_task_dispatch` x3 in ~11s | **Missed batch affordance.** Journal says "3 parallel codex workers"; work was parallel, tool use was serial. |
| 2 | 2026-05-18 17:47 + 17:59 | Panelsmith | `:437`-`:440` grid-test stubs | `engineer_batch_dispatch` x2 | Correct batch use for independent stub workers. |
| 3 | 2026-05-14 19:51 | Panelsmith | PM OSS wave: `:433`, `:432`, `:425`, `:426`, `:429`→`:430` cluster; `:431` held | Single `engineer_batch_dispatch`, then serial recoveries | Correct primary batch use; later serial dispatches were recovery from deferred/handoff DOA. |
| 4 | 2026-05-14 19:51-19:54 | Courier | PM OSS wave: `:427`, `:428`, `:434` parallel; `:435` queued same-agent behind `:434` | `engineer_task_dispatch` x4 | **Missed batch for the three independent starts.** Serial same-agent queue for `:435` was justified by file overlap. |
| 5 | 2026-05-14 13:47 | Panelsmith | AM OSS wave: `:420`, `:421`, `:413`→`:414`, `:419` | Single `engineer_batch_dispatch` | Correct batch use; produced useful evidence of queue/handoff failure modes. |
| 6 | 2026-05-14 13:48-15:05 | Courier | AM supervisor/message-history wave: independent `:418`/`:417`, then `:415`→`:411`, then `:416`/`:422` | Mostly `engineer_task_dispatch` | Mixed. Some parallel-safe starts were serial; later sequencing was deliberate review/merge checkpointing. |
| 7 | 2026-05-14 13:12-13:27 | Courier | Engineer-discretion `:391` then `:412` | `engineer_task_dispatch` x2 | Serial by design: code-read and clean review boundary first. Not a missed parallel case. |
| 8 | 2026-05-10 11:19 | Slack-era engineer | Phase 8 bundle `SLACK:55`-`:59` | Single `engineer_batch_dispatch` | Correct batch use for independent bundle. |
| 9 | 2026-05-10 15:19-15:22 | Atlas engineer | `ATLAS:47`, `ATLAS:48` | `engineer_task_dispatch` x2 | Serial likely justified by critical-path / integration risk; not enough evidence to call missed. |
| 10 | 2026-05-09 16:08-16:32 | Panelsmith | UI polish waves `:394`/`:402`/`:398`/`:405`, then `:399`/`:403`→`:404`/`:406`→`:395`, then `:407`/`:400` | Three `engineer_batch_dispatch` waves | Correct batch use with same-agent clusters. |

Ratios from this table:

- Primary batch use in 5/10 recent multi-task waves.
- Primary serial `task_dispatch` use in 5/10 waves.
- For clearly parallel-safe independent starts, the main missed cases are **Courier's current Tier 1 research wave** and **Courier's PM OSS wave**. Both still launched parallel workers quickly; the issue is tool affordance / consistency more than wall-clock serialization.

## Prompt audit

The actual engineer prompt is built in `torque/engineer.py`, not `torque/server_prompts.py`.
`torque/server_prompts.py` handles worker-side Torque prompt/postscript helpers and deliverable awareness; `server.py` imports `build_engineer_system_prompt` from `torque.engineer`.

Relevant current guidance:

- `torque/engineer.py` defines a wave as "the set of streams/tasks you intentionally activate in parallel."
- Dispatch strategy says to use separate agents for independent work and stagger merge-heavy work touching the same areas.
- Wave planning says to dispatch in short waves, fill open slots with one complex task plus simpler parallel work, and pause before widening risky user-visible/runtime-sensitive work.
- Torque mechanics says: "Use `engineer_batch_dispatch` with a shared `agent_group` when several ordered tasks should stay on one worker..." and separately says to use `engineer_task_dispatch(agent=...)` for existing agents.
- Operating policy only says: when calling `engineer_batch_dispatch` without `max_concurrent`, use the default concurrency setting.

Gap:

- The prompt **does not explicitly say**: "For N>1 ready independent tasks in the same wave, default to `engineer_batch_dispatch`."
- The only direct `engineer_batch_dispatch` guidance is attached to `agent_group` / ordered same-agent mechanics, so an engineer can reasonably infer that batch is mainly for clusters/queues, not independent parallel starts.
- The default `wave_size_preference` is `small`, and the prompt repeatedly says "short waves" / "pause before widening." That is good safety guidance, but without a batch decision tree it can reinforce manual task-by-task dispatch.

## Tool surface audit

Tool specs live in `torque/mcp_engineer_tools/tool_specs.py`.

Current descriptions:

- `engineer_task_dispatch`: "Dispatch task; create an agent unless `agent` is set..."
- `engineer_batch_dispatch`: "Dispatch tasks in order with a concurrency cap; excess entries persistently queue and auto-dispatch as slots open. Same `agent_group` values share one agent."

Implementation lives in `torque/mcp_tools_shared.py` under normalized tool name `batch_dispatch`.

Affordance gaps:

1. The first words are "Dispatch tasks in order," which reads sequential, not "start a parallel wave."
2. `agent_group` is both powerful and confusing: it is an affinity key for same-agent queues, not a concurrency group. `TORQUE:389` already captures that `max_concurrent` errors do not distinguish engineer-group cap from `agent_group` affinity.
3. Batch has less per-task expressiveness than `engineer_task_dispatch`:
   - Batch has a global `provider`, but no per-entry provider/model/reasoning/name override.
   - Task dispatch supports `agent_type`, `command`, `model`, `reasoning_effort`, `name`, and `agent`.
   - Engineers who want explicit names/providers or checkpointed existing-agent queues naturally reach for `task_dispatch`.
4. Partial failures are returned as per-entry result objects while the MCP call itself is still non-error. That is appropriate for dependency-blocked entries, but it makes batch feel less deterministic than simple one-task calls.

Known batch UX gaps already filed:

- `TORQUE:389`: clarify `max_concurrent` semantics in error message + tool description.
- `TORQUE:390`: fix cap-stuck-at-enqueue when retrying an already-queued task with a higher cap.

## Engineer reasoning findings

Top reasons engineers go serial, with evidence:

1. **Prompt/tool framing makes serial task dispatch feel like the normal primitive.**
   - Current prompt says independent work should use separate agents, but only names batch in the same-agent `agent_group` mechanics paragraph.
   - Current tool description emphasizes order and caps rather than "parallel wave."
   - Evidence: Courier's 2026-05-18 journal says "3 parallel codex workers" and "Parallel-3 dispatches," but actual MCP calls were three separate `engineer_task_dispatch` calls.

2. **Batch has known reliability/UX footguns, so avoidance can be rational.**
   - `TORQUE:389` and `TORQUE:390` are open backlog tasks for batch semantics and cap retry behavior.
   - 2026-05-14 journals record deferred-queue auto-promote DOA for `:419`, `:426`, `:429`, and cluster handoff DOA for `:413`→`:414` / `:429`→`:430` shapes.
   - Those failures required explicit `engineer_task_dispatch` recoveries and taught engineers to monitor or manually dispatch follow-ups.

3. **Serial tool calls still achieve parallel worker execution for small N.**
   - Current Tier 1 research: three task-dispatch calls landed within ~11 seconds.
   - Courier PM OSS wave: independent starts were seconds apart, with a deliberate same-agent queue for `:435`.
   - For N=2-3, the wall-clock penalty is negligible, so the behavioral incentive to learn batch is weak.

4. **Engineers intentionally serialize for review/merge boundaries.**
   - Panelsmith held `:431` out of the batch to keep a clean feature/review boundary after `:430` landed.
   - Courier explicitly chose sequential `engineer_task_dispatch` for `:415`→`:411`, citing per-task provider uncertainty and a desire for a merge checkpoint between cluster legs.
   - Courier sequenced `:391` then `:412` after an engineer-personal code-read to preserve clean blast radius.

5. **Architect envelopes often describe concurrency intent, but not the exact dispatch primitive.**
   - Envelopes say "parallel," "cluster," "hold," and "waves of 3-4," but rarely say "use `engineer_batch_dispatch` for these independent tasks."
   - Engineers correctly infer the intended topology but choose the familiar primitive.

## Failure modes when engineers do dispatch in parallel

Observed failure modes are real but manageable:

1. **Deferred queue auto-promote DOA**
   - Batch-deferred tasks did not auto-promote after slots freed (`:419`, `:426`, `:429`).
   - Health did not always catch this because unassigned Backlog tasks have no worker health signal.
   - Recovery: explicit `engineer_task_dispatch`.

2. **Same-agent cluster handoff DOA**
   - Queued same-agent follow-ups sometimes failed to resume even with `agent_id` wired and worker idle.
   - Recovery: explicit `engineer_task_dispatch(task=..., agent=...)` or engineer nudge.

3. **Stale-base / rebase churn**
   - Parallel branches often hit stale-base warnings as siblings merge.
   - Usually resolved with `engineer_rebase`; not a reason to avoid all parallelism.

4. **File overlap / merge conflicts**
   - Example: `:420` conflict in `static/js/render.js` during a parallel UI wave; manual resolution kept both handlers.
   - This argues for the existing decision rule: parallelize disjoint surfaces, cluster or serialize same-surface work.

5. **Review-gate ordering and diff scope**
   - Large or mandatory-review tasks may need to be held out of broad batches so reviewer scope stays clean.
   - This is a valid serial-by-design exception, not a tooling failure.

## Recommendations

### Recommendation 1 — Promote/fix batch reliability UX before increasing pressure

**Derived task shape:** `oneshot/fix` or small `feature/implement` depending on whether it combines existing backlog.

**Scope:**

- Implement or promote `TORQUE:389` and `TORQUE:390` before telling engineers to batch more aggressively.
- Add/adjust tests around:
  - `max_concurrent` error message includes engineer-group name, active count, and cap.
  - Retrying an already queued task with a higher cap updates the stored queue entry or returns an explicit `cap_raised` result.
  - Lowering cap does not silently change the queue.

**Likely files:**

- `torque/mcp_engineer_tools/tool_specs.py`
- `torque/mcp_tools_shared.py`
- `torque/state.py`
- `tests/test_mcp.py`
- `tests/test_state.py`
- `tests/test_server_dispatch.py` / `tests/test_server_self_dispatch.py` if pump behavior is touched

**Why first:** Stronger prompt guidance without reducing known batch friction may simply create more batch queue recovery work.

### Recommendation 2 — Add an explicit engineer prompt decision tree

**Derived task shape:** `oneshot/improvement`, prompt/test only.

**Scope:** Update `torque/engineer.py` engineer prompt text so dispatch guidance says:

- Default to `engineer_batch_dispatch` when activating N>1 ready independent tasks in the same turn with the same provider/default launch settings.
- Use no `agent_group` or unique `agent_group`s for independent workers; use shared `agent_group` only for intentional same-agent sequential queues.
- Use `engineer_task_dispatch` when:
  - There is only one task.
  - You need an existing `agent=...` recovery / handoff.
  - You need per-task provider/model/name/command overrides.
  - You are deliberately waiting for a merge/review/verification checkpoint before starting the next task.
  - Tasks touch the same risky surface and should not widen in parallel.
- Keep current caution around short waves, user-visible/runtime-sensitive changes, and review boundaries.

**Likely files:**

- `torque/engineer.py`
- `tests/test_engineer_prompt.py`

**Acceptance:** Prompt tests assert the new decision tree contains the batch default and serial exceptions. No runtime behavior change.

### Recommendation 3 — Reword `engineer_batch_dispatch` tool schema around parallel waves

**Derived task shape:** `oneshot/improvement`, tool-spec/test only unless response payload changes.

**Scope:** In `torque/mcp_engineer_tools/tool_specs.py`:

- First sentence should say it is the preferred tool to start a multi-task dispatch wave.
- Clarify: entries are evaluated in list order, but independent entries without shared `agent_group` start as separate workers up to the engineer-group `max_concurrent` cap.
- Clarify `agent_group` is same-agent affinity, not a capacity group.
- Clarify `max_concurrent` is engineer-group active worker cap.
- Consider adding a short example in the description or adjacent docs:
  - independent parallel: `[{"task": ":1"}, {"task": ":2"}]`
  - same-agent cluster: `[{"task": ":1", "agent_group": "history"}, {"task": ":2", "agent_group": "history"}]`

**Likely files:**

- `torque/mcp_engineer_tools/tool_specs.py`
- `tests/test_mcp.py` if schema wording is asserted

### Recommendation 4 — Add a low-noise dispatch affordance / metric

**Derived task shape:** `feature/research` or small `feature/implement` after recommendations 1-3 land.

**Scope options:**

- Add a lightweight MCP-call audit surface to show recent dispatch waves by tool (`batch` vs `task_dispatch`), using existing `mcp_idempotency` / `engineer_mcp_calls` plumbing where possible.
- Or add a soft hint when the same engineer calls `engineer_task_dispatch` for multiple new-agent tasks within a short window and none used per-task overrides: "For future N>1 independent starts, `engineer_batch_dispatch` can launch this as one wave."

**Caution:** Avoid noisy nags. Do not hint for existing-agent recovery, queued follow-ups, per-task overrides, dependency gating, or explicit checkpointed sequencing.

**Likely files if implemented:**

- `torque/mcp_tools_shared.py` or MCP handler/idempotency layer
- `torque/mcp_engineer.py` / `torque/mcp_engineer_tools/tool_specs.py` if exposed as a tool
- Tests likely in `tests/test_mcp.py` and possibly `tests/test_mcp_reliability.py`

### Recommendation 5 — Do not change global concurrency defaults yet

Do **not** increase `default_worker_concurrency`, change `wave_size_preference`, or force architects to choose parallelism at filing time as a first move. The recent data shows engineers are often making valid serial decisions for review boundaries, same-file risk, or batch recovery. Prompt/tool clarity should come before policy default changes.

## Implementation plan for derived work

1. **Reliability/UX patch for batch cap semantics**
   - Implement `:389` and `:390` or file a combined task that explicitly includes both.
   - Update `mcp_tools_shared.py`, `state.py`, and tests.
   - Run targeted Python tests plus `make test` before merge.

2. **Prompt decision-tree patch**
   - Edit `torque/engineer.py` in the Dispatch Strategy / Wave Planning / Torque Mechanics area.
   - Keep text compact; avoid duplicating the whole prompt.
   - Add `tests/test_engineer_prompt.py` assertions.
   - Run `python -m unittest tests.test_engineer_prompt` and full `make test`.

3. **Tool schema wording patch**
   - Edit `torque/mcp_engineer_tools/tool_specs.py` for `engineer_batch_dispatch` and `max_concurrent` / `agent_group` descriptions.
   - Add a targeted schema test only if the project accepts wording assertions; otherwise rely on prompt test + smoke.
   - Run `python -m unittest tests.test_mcp tests.test_mcp_reliability` and full `make test`.

4. **Optional follow-up: dispatch metric/hint**
   - File only after observing whether prompt/tool wording changes improve behavior.
   - Keep hints advisory and scoped to clear missed-batch shapes.

## Out of scope / not recommended now

- A unified "smart dispatch" API that decides batch vs serial automatically. This may be valuable later, but it is a larger API/behavior shift and not necessary before prompt/tool fixes.
- Forcing every N>1 wave through `engineer_batch_dispatch`. Serial is correct for dependencies, same-file risk, clean review boundaries, existing-agent recovery, and per-task launch overrides.
- Changing architect task filing defaults to encode parallelism. Architect envelopes already express tier/cluster intent; the immediate gap is engineer tool choice and batch reliability.
- Treating `engineer_task_dispatch` x3 within 10 seconds as equivalent to harmful one-at-a-time worker execution. It is a consistency/affordance issue, not always a throughput issue.

## Approval path

Engineer approval is enough for this research plan artifact. The recommended derived tasks are bounded and stay within the approved product direction. Human approval is only needed if a follow-up expands into a new unified dispatch API, global concurrency default changes, or architect-side automatic scheduling policy changes.
