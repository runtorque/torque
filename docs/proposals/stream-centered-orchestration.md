# Proposal: Stream-Centered Orchestration

**Status**: Draft proposal  
**Audience**: product, Engineer orchestration, backend/state, frontend UI  
**Primary goal**: unify streams, same-agent queues, queue gates, and waves into one coherent operating model

---

## Executive summary

Loom today is already good at tracking **tasks**, **agents**, and **branch boundaries**. The next step is to make the system equally good at tracking the thing the designated engineer actually reasons about most of the time:

> a shared branch/worktree slice of work that evolves through implementation, review, blocker-fix loops, validation, and merge.

This proposal calls that slice a **stream**.

In the proposed model:

- a **product task** is a user-visible ask or deliverable
- a **workflow task** is a review/fix/validation/conflict-resolution step that moves a stream safely
- a **visibility item** is communication or status context that should be visible without pretending to be product work
- a **derived task** is an explicit workflow handoff or follow-up inside that work
- a **stream** is the branch/worktree-level execution and review state machine that contains those tasks
- a **wave** is the set of streams/tasks the designated engineer intentionally activates in parallel

The central simplification is:

- **waves schedule work across streams**
- **streams manage continuity inside a branch/worktree**
- **queue priority, queue state, review blockers, and merge gates all become stream behavior**

That replaces the current situation where the designated engineer often has to reconstruct a branch's real state from a mix of tasks, reviews, verification notes, agent state, and branch-boundary summaries.

---

## First, a clarification on the simplified mental model

A good simplified reading is:

> Waves group tasks which are dispatched into streams. Each stream has its own worktree, and only one task/agent at a time should actively own the mutable implementation lane for that stream, which avoids conflicts.

That is **mostly correct**, with one important nuance:

### Correct core intuition

- a stream is the **reviewable branch/worktree slice**
- tasks can be routed into that stream over time
- queued same-stream work should not compete with the current blocker/review state
- one task should be the **foreground mutable owner** of the stream at a time

### Important nuance

A stream may still temporarily involve **more than one agent**:

- an **implementation agent** owns the mutable branch/worktree lane
- a **review agent** may inspect the branch in parallel and derive a blocker-fix task back to the parent implementation lane
- a **human validation gate** may block merge without any active implementation

So the design goal is not literally:

> only one task or agent may ever touch the stream

It is more precisely:

> only one task at a time should own the stream's foreground mutable execution lane, while review/validation tasks can exist around it without competing for branch ownership or queue priority.

That distinction matters because it lets Loom keep the branch conflict-free **without** losing explicit reviews, blocker fixes, or validation gates.

---

## Problem statement

During complex orchestration, the designated engineer is rarely thinking in terms of isolated tasks. It is usually thinking in terms of questions like:

- What is the current state of this branch?
- Is this branch implementing, reviewing, fixing blockers, waiting on manual smoke, or ready to merge?
- Which queued follow-up tasks on this branch are real next work versus intentionally paused work?
- Did review find a blocker that should preempt future same-agent tasks?
- Is this branch clean but blocked on human validation?
- What should happen next without manually steering the agent?

Today Loom has most of the raw ingredients to answer those questions, but they are scattered across:

- board task lanes and statuses
- derived task chains
- agent current task state
- worktree boundaries and successor links
- review task results
- verification notes
- digest events

This causes several pains:

1. **The Engineer reconstructs stream state manually** instead of reading it directly.
2. **Same-agent queued work** can continue when a review blocker should have preempted it.
3. **Human validation** is represented as notes instead of as a first-class gate.
4. **Review/fix/re-review loops** create bookkeeping noise that obscures the real critical path.
5. **Backlog planning (waves)** gets mixed up with **branch-internal state management**.

The missing abstraction is the stream.

---

## Design principles

1. **Tasks remain the audit trail**  
   We should not replace tasks with streams. Tasks are the visible record of asks, handoffs, reviews, fixes, and approvals.

2. **Streams become the continuity model**  
   Streams represent the lifecycle of one reviewable branch/worktree slice across multiple tasks.

3. **Waves stay about scheduling, not branch traffic control**  
   The Engineer should choose which streams are active in parallel. It should not need to micromanage blocker preemption inside each active stream.

4. **One mutable foreground owner at a time**  
   A stream should have one foreground mutable task/lane at a time. Review and validation may exist around it, but should not compete with the stream queue.

5. **Queue state is stream state made visible**  
   Queue priority, pauses, and resumption should be explained as stream behavior, not as special-case ad hoc queue logic.

6. **Human validation must be a real gate**  
   If a branch is code-clean but waiting on runtime smoke or operator sign-off, the stream should say so explicitly.

7. **MVP should be computed first**  
   Before adding a new persistent database object, Loom should prove the model by computing stream state from existing task, boundary, review, and verification data.

---

## Core definitions

### Task

A **task** is a unit of work tracked on the board.

Examples:

- Add Events tab to the Agent panel
- Add Worklog tab to the Agent panel
- Keep Engineer Events next-dispatch timing accurate without full-panel rerender
- Review self-dispatch prompt submission fix
- Resolve merge conflicts for Engineer ownership branch

A product task answers:

> What specific user-facing or operator-facing outcome are we trying to deliver?

Product tasks are still the right place for:

- titles
- descriptions
- labels
- assignees
- review outputs
- verification notes
- history and artifacts

### Workflow task

A **workflow task** is work created to move a stream safely through implementation, review, fix, validation, or merge.

Examples:

- Review Events implementation
- Fix self-dispatch priming regression
- Resolve merge conflict in db/state/modals files
- Validate Engineer Events stream after manual smoke

A workflow task answers:

> What operational step must happen so the stream can continue safely?

Workflow tasks are first-class and auditable, but they should not be confused with product scope. Their main purpose is to make safety, review, and ownership transitions explicit.

### Visibility item

A **visibility item** is not product scope and usually should not be treated as a real execution task. It exists so the user can see that communication or orchestration activity happened.

Examples:

- a Engineer note to a worker
- a worker reply to the designated engineer
- a prompt such as "Proceed with the derived task you just created"
- a short queue-control or reprioritization message

A visibility item answers:

> What communication or orchestration context should the user be able to see?

Visibility items should be visible in task/agent/stream history, but they should not appear as root/product tasks and should not compete with real stream execution state.

### Derived task

A **derived task** is a workflow-generated follow-up created from another task.

Examples:

- Review X
- Fix the issues found
- Resolve merge conflict
- Create a validation step after review

A derived task answers:

> What happened next in the workflow, and who owns this next step?

Most derived tasks are **workflow tasks**. Some communication follow-ups may still be derived records internally, but they should be presented to users as visibility items rather than as product/root tasks.

Derived tasks are useful because they preserve:

- ownership transitions
- review lineage
- blocker-fix lineage
- explicit auditability
- handoff visibility

Derived tasks should remain first-class.

### Stream

A **stream** is the branch/worktree-level execution and review state machine that ties related tasks together.

A stream answers:

> What is the state of this shared branch/worktree slice right now?

A stream may include:

- multiple product/root tasks
- multiple workflow tasks
- one mutable implementation lane
- zero or more queued future same-stream product tasks
- zero or one active blocker-fix path
- zero or one current review gate
- zero or one validation/merge gate
- a lightweight communication/visibility timeline

The stream is the place where Loom should answer:

- what branch is this?
- which product tasks, workflow tasks, and visibility items belong to it?
- what task is foreground now?
- what is queued next?
- what is blocking progress?
- is it ready to merge?
- what should happen next?

### Wave

A **wave** is the set of streams and/or standalone tasks the designated engineer intentionally activates together.

A wave answers:

> What are we working on in parallel right now, and why this mix?

A wave should capture:

- concurrency choice
- risk balance
- product-surface grouping
- intentional activation of streams/tasks

A wave does **not** manage branch-internal blocker logic. That belongs to streams.

---

## The hierarchy

The proposed hierarchy is:

```text
Wave
└── Stream A
    ├── Root task: Add Events tab
    ├── Root task: Add Worklog tab
    ├── Derived task: Review Events implementation
    ├── Derived task: Fix review blocker
    └── Root task: Countdown follow-up

Wave
└── Stream B
    ├── Root task: Interactive agent detail panel
    ├── Derived task: Review implementation
    ├── Derived task: Fix blocker
    └── Root task: Quick-label editor persistence fix
```

And the conceptual roles are:

- **Wave** = scheduling unit
- **Stream** = execution/review/merge unit
- **Task** = work/audit unit
- **Derived task** = workflow transition unit

---

## Why stream state should not be a root-task field

This is an important design choice.

A root task is often too narrow to represent the actual stream.

Example from recent work:

- LOOM:333 — Add Events tab
- LOOM:334 — Add Worklog tab
- LOOM:342 — Countdown fix
- multiple review/fix/re-review loops
- all on the same branch/worktree

There was clearly **one stream**, but multiple root tasks.

If stream state were attached to LOOM:333 alone, then:

- LOOM:334 would look like a separate branch state when it was not
- LOOM:342 would look like a separate story when it was actually a follow-up on the same stream
- merge readiness would be ambiguous

So the right model is:

- tasks can **belong to** a stream
- tasks can **display** stream state
- but the stream should have its own identity, derived from the branch/worktree slice, not from any one root task

---

## The stream object

### MVP: computed read model

The initial implementation should be a **computed object**, not a new persisted database table.

The stream can be synthesized from:

- `repo_root + branch`
- worktree boundary state
- successor tasks (`resume_after_boundary_task_id`)
- current agent ownership
- active review/fix tasks
- verification state and notes
- latest reviewed commit info

### Proposed canonical fields

```json
{
  "stream_id": "stream:/repo::branch",
  "group": "Loom",
  "repo_root": "/path/to/repo",
  "branch": "loom/add-events-tab...",

  "agent_id": "837241c8",
  "agent_name": "add-events-tab-to-the-engineer-panel-for",
  "agent_slug": "add-events-tab-to-the-engineer-panel-for",

  "foreground_task_id": "LOOM:342",
  "foreground_task_title": "Keep Engineer Events next-dispatch timing accurate...",

  "product_task_ids": ["LOOM:333", "LOOM:334", "LOOM:342"],
  "workflow_task_ids": ["LOOM:333:4", "LOOM:333:5", "LOOM:342:1"],
  "queued_task_ids": [],
  "started_task_ids": ["LOOM:342"],
  "visibility_items": [
    {"kind": "engineer_message", "summary": "Reprioritized blocker fix before queued work"}
  ],

  "latest_boundary_task_id": "LOOM:342:1",
  "latest_boundary_task_title": "Review Engineer Events countdown update",
  "latest_boundary_recorded_at": "...",
  "latest_reviewed_commit_sha": "fbcf26b...",

  "active_review_task_id": "",
  "active_blocker_task_id": "",

  "state": "awaiting_human_validation",
  "code_state": "reviewed_clean",
  "validation_state": "pending_human_validation",
  "merge_state": "not_ready",

  "gate_reason": "Live/manual Engineer-panel smoke pending",
  "recommended_next_action": "merge_after_validation",

  "branch_advanced": false,
  "partial_review_safe": true,
  "last_activity_at": "..."
}
```

### Recommended derived top-level states

These should be normalized and human-readable:

- `implementing`
- `reviewing`
- `fixing_blockers`
- `awaiting_human_validation`
- `ready_to_merge`
- `merged`

These states describe the **stream**, not any single task. Product tasks, workflow tasks, and visibility items are all inputs into the computed stream state, but they should not each pretend to be the state machine themselves.

Supporting substate fields make it easier to reason precisely:

- `code_state`: `implementing | reviewed_clean | review_blocked | merge_conflict | merged`
- `validation_state`: `none | automated_only | pending_human_validation | validated | waived`
- `merge_state`: `not_ready | ready | merged`

---

## Stream queue: what it is and what it is not

The queue should not be a separate orchestration subsystem.

It should be understood as:

> the ordered continuation plan for future product work inside a stream

That means queued tasks on the same agent/worktree are not just a FIFO list. They are part of the stream's execution plan. Workflow tasks can preempt that queue; visibility items should never compete with it.

### Queue states

Queued **product tasks** inside a stream should expose queue-specific state, for example:

- `queued`
- `paused_by_blocker`
- `paused_by_review`
- `paused_by_validation`
- `held`
- `ready_to_resume`

These should be **metadata/badges**, not board lanes.

### Queue gate

A stream should expose at most one current queue gate at a time, such as:

```json
{
  "gate_type": "review_blocker",
  "blocking_task_id": "LOOM:333:5",
  "source_task_id": "LOOM:333:4",
  "reason": "Self-dispatch priming regression must be fixed before resuming queued work",
  "clears_when": "review_passes"
}
```

Gate types can include:

- `review_blocker`
- `merge_conflict`
- `human_validation`
- `manual_hold`

### Foreground task

At any given time, the stream should have exactly one **foreground mutable task**.

That is the task that currently owns the implementation lane of the stream. It may be a product task or a workflow fix task, depending on stream state.

Review tasks may exist around it, but should not compete with the mutable lane. Visibility items should be rendered as context, not foreground execution.

---

## How queue behavior becomes stream behavior

This is where the ideas converge.

### Rule: review blockers preempt the queue

If review finds a blocker on the current stream, Loom should:

1. set stream state to `fixing_blockers`
2. make the blocker-fix task the new foreground task
3. mark future queued same-stream work as `paused_by_blocker`
4. prevent the queue from continuing until the blocker gate clears

### Rule: review completion clears the gate

When blocker-fix review passes, Loom should:

1. clear the blocker gate
2. recompute stream state
3. mark the next eligible queued task as `ready_to_resume`
4. auto-resume that queued task if stream policy allows it

### Rule: validation may pause the queue

If a stream becomes code-clean and review-clean but still requires manual smoke or human approval, Loom should be able to represent that as:

- stream state = `awaiting_human_validation`
- queue gate = `human_validation`
- queued tasks = `paused_by_validation` if continuation should pause

In the MVP, Loom only needs to **represent** this gate clearly. Rich validation workflow controls can come later once the stream read model is in place.

### Rule: merge conflict preempts the queue

If merge or rebase fails and conflict resolution is required, Loom should:

- set stream state to `fixing_blockers` or a more specific `merge_conflict` code_state
- make the conflict-resolution task foreground
- pause future queued work automatically

---

## Waves: what they are for

A wave should remain intentionally simple.

A wave is the designated engineer's answer to:

> Which streams or standalone tasks should be active in parallel right now?

A wave captures planning decisions such as:

- concurrency level
- grouping by product surface
- risk spread
- keeping one risky stream and a few easier streams active together

### Example wave

```text
Wave 12
├── Stream A: Engineer Events + Worklog
├── Stream B: Interactive agent detail panel
└── Stream C: Self-dispatch prompt bug
```

After dispatching the wave, the designated engineer should not need to micromanage branch-internal priority. Each stream should handle:

- review loops
- blocker preemption
- queue pauses
- queue resumption
- merge gates

### Why this matters

Without streams, the designated engineer ends up doing branch traffic control manually.
With streams, the designated engineer can think at the right level:

- Which streams should be active now?
- Which streams are blocked?
- Which streams are ready to merge?
- Which streams are waiting on human validation?

That is a much better product model.

---

## Wireframes

## 1) Engineer board: current wave overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Engineer Board                                                        │
├──────────────────────────────────────────────────────────────────────┤
│ Active Wave: Wave 12                                                │
│ Goal: 1 risky stream + 2 smaller parallel streams                   │
│                                                                      │
│ Streams in wave                                                      │
│                                                                      │
│  [A] Engineer Events + Worklog                                         │
│      state: fixing_blockers                                          │
│      branch: loom/add-events-tab-to-the-engineer-p-837241c             │
│      foreground: Fix self-dispatch priming regression                │
│      gate: waiting for re-review                                     │
│      queue: Add Worklog tab (paused by blocker)                      │
│                                                                      │
│  [B] Interactive agent detail                                        │
│      state: implementing                                             │
│      branch: loom/make-the-agent-detail-panel-in...                  │
│      foreground: Keep labels quick editor open                       │
│      queue: none                                                     │
│                                                                      │
│  [C] Self-dispatch prompt bug                                        │
│      state: reviewing                                                │
│      branch: loom/fix-self-dispatch-so-derived-task...               │
│      foreground: Review self-dispatch prompt submission fix          │
│      queue: none                                                     │
└──────────────────────────────────────────────────────────────────────┘
```

## 2) Stream card

```text
┌───────────────────────────────────────────────────────────────┐
│ Stream: Engineer Events + Worklog                               │
├───────────────────────────────────────────────────────────────┤
│ Branch        loom/add-events-tab-to-the-engineer-p-837241c     │
│ State         Awaiting human validation                        │
│ Code          Reviewed clean                                   │
│ Validation    Pending manual smoke                             │
│ Merge         Not ready                                        │
│ Gate          Live/manual Engineer-panel smoke pending           │
│ Next action   Merge after validation                           │
│ Latest commit fbcf26b                                          │
│ Roots         LOOM:333, LOOM:334, LOOM:342                     │
│                                                              │
│ Foreground    none                                             │
│ Queue         none                                             │
└───────────────────────────────────────────────────────────────┘
```

## 3) Stream with blocker preemption

```text
┌───────────────────────────────────────────────────────────────┐
│ Stream: Events + Worklog                                      │
├───────────────────────────────────────────────────────────────┤
│ State         Fixing blockers                                 │
│ Gate          Review blocker                                  │
│ Reason        Self-dispatch priming regression must be fixed  │
│                                                              │
│ Foreground    LOOM:333:5  Fix review blockers                 │
│                                                              │
│ Queue                                                         │
│   1. LOOM:334   Add Worklog tab            paused_by_blocker  │
│   2. LOOM:342   Countdown update           queued             │
│                                                              │
│ Auto-resume    When blocker review passes                     │
└───────────────────────────────────────────────────────────────┘
```

## 4) Review and resume flow

```text
Implement task A
      │
      ▼
 Review task A
      │
      ├── no blockers ───────────────┐
      │                              │
      ▼                              ▼
 blocker found                 stream becomes reviewed_clean
      │                              │
      ▼                              │
 derive blocker fix                  │
      │                              │
      ▼                              │
 stream state = fixing_blockers      │
 queue = paused_by_blocker           │
      │                              │
      ▼                              │
 implement blocker fix               │
      │                              │
      ▼                              │
 review blocker fix                  │
      │                              │
      ├── fails ──> stay fixing      │
      │                              │
      └── passes ────────────────────┘
                     │
                     ▼
             clear queue gate
                     │
                     ▼
             next task = ready_to_resume
                     │
                     ▼
             auto-resume if policy allows
```

## 5) Relationship diagram

```text
          Planner / Engineer
                 │
                 ▼
               Wave
       (parallel scheduling set)
                 │
        ┌────────┴────────┐
        ▼                 ▼
     Stream A          Stream B
 (branch/worktree)  (branch/worktree)
        │                 │
   ┌────┼────┐       ┌────┼────┐
   ▼    ▼    ▼       ▼    ▼    ▼
 root review fix    root review fix
 task task   task   task task   task
```

---

## Product behavior summary

### What a stream owns

A stream owns:

- branch/worktree identity
- current foreground mutable task
- queue of future same-stream tasks
- active review/fix loop state
- merge conflict state
- validation gate state
- merge readiness

### What a wave owns

A wave owns:

- which streams/tasks are active in parallel
- why they were grouped together
- concurrency/risk balance

### What product tasks and workflow tasks own

Tasks own:

- the specific ask or workflow step
- title/description/labels
- artifacts/history/verification
- auditability of ownership and transitions

But product tasks and workflow tasks should be distinguishable in presentation and summaries so users can tell scope from process.

### What a derived task owns

A derived task owns:

- explicit workflow continuation
- review handoff
- blocker-fix handoff
- conflict-resolution handoff
- optional communication/audit linkage

---

## UX implications

### Engineer board

Add an **Open Streams** section that shows, for each stream:

- branch / friendly title
- stream state badge
- gate reason
- recommended next action
- related root tasks
- latest reviewed commit
- merge readiness

### Task cards

Tasks that belong to a stream can display a lightweight stream chip:

- `stream: fixing blockers`
- `stream: awaiting validation`
- `stream: ready to merge`

Task cards should also visually distinguish:

- **product tasks** (deliverables / asks)
- **workflow tasks** (review, fix, validation, merge-conflict, etc.)

Visibility items such as Engineer messages should not show up as root/product task cards by default. They belong in history/timeline surfaces, not in the primary backlog narrative.

### Agent detail panel

For the stream-owning implementation agent, show:

- foreground task
- queue
- queue gate reason
- resume/hold controls
- recent visibility items / communication timeline

---

## Product-task vs workflow-task vs visibility-item rules

To keep streams useful without adding new noise, Loom should classify stream members into three presentation buckets:

### Product tasks
These are the deliverables the user actually asked for. They are the things that should naturally appear as root tasks in backlog planning and wave selection.

Examples:

- Add Events tab
- Add Worklog tab
- Keep Engineer Events next-dispatch timing accurate

### Workflow tasks
These are operational steps created so the stream can move safely. They should remain first-class and auditable, but they should be rendered as process, not as product scope.

Examples:

- Review Events stream
- Fix self-dispatch priming regression
- Resolve merge conflicts
- Validate stream after smoke testing

### Visibility items
These exist only so the user can see that communication or steering happened. They should not become root/product tasks and they should not compete with the stream queue.

Examples:

- Engineer asks a worker to reprioritize a blocker fix
- Worker replies acknowledging the new priority
- Engineer sends a short note like "Proceed with the derived task you just created"

### Visibility-item defaults
- Visibility-first by default: storing and rendering the communication should not require creating a board task card.
- Message/thread status should be separate from task status. Useful states include: `informational`, `awaiting_reply`, `acknowledged`, `replied`, and `unread`.
- Visibility items should render as a threaded stream/agent timeline rather than as flat task spam.
- Visibility items should not affect backlog counts, in-progress counts, queue ordering, or wave planning.
- If Loom still stores internal task-like records for correlation, UI layers must classify them as visibility-only and hide them from root/backlog/product views by default.

### Promotion rule
A visibility item should only be promoted into a workflow task when it creates a real completion boundary that needs explicit ownership and closure. Examples might include an explicit validation handoff or a required operator follow-up. Simple steering messages, reprioritization notes, and acknowledgements should remain visibility items.

### Presentation rules
- Product tasks should appear in backlog/wave planning as the main deliverables.
- Workflow tasks should be shown inside the stream as operational steps, ideally collapsed under the stream unless expanded.
- Visibility items should appear in stream history/timeline and agent history, not as root/product task cards by default.
- A Engineer question/message should never be treated as a root/product task. If Loom still stores it as an internal record for correlation, UI layers should classify and render it as visibility-only.

## Proposed controls

### Stream-level controls

- Pause stream queue
- Resume stream queue
- Merge stream
- Mark validation passed / waived
- Move queued task to front
- Hold queued task
- Skip queued task

### Recommended MVP controls

For a minimal first release, start with:

- Resume queued stream task now
- Pause stream queue
- Merge stream (when ready)

The rest can follow.

---

## Recommended API additions

### New read tools

- `engineer_streams_list`
- `engineer_stream_show`

These should expose:

- stream identity
- state
- code_state
- validation_state
- merge_state
- queue
- gate reason
- latest reviewed commit
- related product tasks
- related workflow tasks
- recent visibility items
- optional visibility-thread summaries and statuses
- recommended next action

### Existing summaries

`engineer_board_summary` should be enriched to show streams directly instead of only raw branch-boundary facts.

---

## Implementation approach

## Phase 1 — computed stream overview

Build a read-only computed stream synthesis layer from current task/boundary/review/verification data.

Manual validation is part of Phase 1 at the **read-model level**. The stream must be able to say explicitly that code is clean but merge is still blocked on human/runtime validation.

Likely new module:

- `loom/worktree_streams.py`

Likely consumers:

- `loom/mcp_engineer.py`
- Engineer board UI

Validation behavior included in Phase 1:

- compute `validation_state` as part of the stream object
- surface explicit validation `gate_reason` when manual/runtime checks remain
- ensure a code-clean but validation-pending branch reads as `awaiting_human_validation`, not merely idle
- compute `recommended_next_action` values such as `run_manual_validation` or `merge_after_validation`

No DB migration in this phase.

## Phase 2 — stream queue and gate behavior

Add stream-local queue and gate semantics:

- queue states
- blocker preemption
- auto-resume
- validation pause behavior
- queue controls

This phase makes queue priority a stream behavior, not an ad hoc manual correction.

Validation behavior deferred to Phase 2+ at the **workflow/control level**:

- explicit validation controls (mark passed / waived / still pending)
- optional validation tasks or approval gates when explicit closure is needed
- policy knobs for whether validation pauses future queued product work
- richer validation history/sign-off surfaces

## Phase 3 — optional persistence / richer wave model

If the computed model proves useful, consider:

- persisted stream identity/state
- lightweight persisted wave history / current wave metadata
- richer resume/hold/reorder controls

---

## Non-goals

This proposal does **not** require:

- replacing tasks with streams
- removing derived tasks
- turning Engineer communication visibility into root/product tasks
- redesigning all communication as workflow tasks
- introducing a `branch_streams` table in the MVP
- redesigning all board lanes around stream states
- building a full scheduling UI before stream state exists

---

## Why this model is simpler, not more complex

At first glance, adding “streams” sounds like another abstraction.
In practice, it reduces complexity because it matches how the designated engineer already thinks.

Today the designated engineer mentally groups:

- several product tasks
- several workflow tasks
- some communication visibility
- one branch
- one mutable implementation lane
- one review/fix loop
- one merge gate

into one conceptual unit anyway.

The proposal simply gives that unit a name and a proper read model.

This is especially important for manual validation: the first implementation goal is to make validation visible as a stream gate, not to build a full validation workflow engine on day one.

So the simplification is:

- **waves** say what runs in parallel
- **streams** say what is happening on each branch
- **product tasks** say what is being delivered
- **workflow tasks** say how the stream is being moved safely
- **visibility items** show communication without polluting the product/workflow model

That is the clean connection between all the recommendations.

---

## Final recommendation

Loom should adopt a **stream-centered orchestration model**:

1. treat **streams** as the branch/worktree-level execution state machine
2. treat **queue state and priority** as stream behavior
3. treat **waves** as the scheduling layer across streams
4. keep **tasks and derived tasks** as the explicit, auditable work records inside each stream
5. keep **Engineer/worker communication** as visibility-thread state by default, promoting it to workflow tasks only when explicit closure semantics are needed

If implemented this way, Loom will become much easier to reason about in exactly the places that currently slow orchestration down:

- review/fix loops
- same-agent queued follow-ups
- branch merge readiness
- manual validation gates
- wave planning across multiple active slices of work
