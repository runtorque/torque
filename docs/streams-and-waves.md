# Streams & Waves

Torque now uses a **stream-centered orchestration model** for the designated engineer.

This page explains:

- what **streams** are
- what **waves** are
- how they relate to **tasks**, **derived tasks**, and **visibility items**
- how **queue gates** and **auto-resume** work
- what is **already implemented**
- what is still a **future phase**

If you want the design rationale rather than the operator-facing explanation, see the proposal in [`docs/proposals/stream-centered-orchestration.md`](proposals/stream-centered-orchestration.md).

---

## The short version

Torque now distinguishes between two levels of orchestration:

- a **wave** is the set of work the designated engineer chooses to run in parallel
- a **stream** is one branch/worktree execution lane that moves through implementation, review, blocker fixes, validation, and merge

Put simply:

> Waves schedule work across streams.  
> Streams manage continuity inside a branch.

That means the designated engineer no longer has to infer a branch's real status from a messy pile of loosely-related tasks, review notes, and agent state.

---

## Why this model exists

Before streams, Torque already tracked:

- tasks
- agents
- worktrees
- reviews
- branch boundaries
- verification notes

That was enough to get work done, but not enough to explain the thing the designated engineer actually reasons about most of the time:

> “What is the current state of this branch-sized slice of work?”

Examples of the questions the designated engineer needs answered quickly:

- Is this branch still implementing?
- Is it on review?
- Did review find blockers?
- Is future queued work intentionally paused?
- Is the code clean but waiting on manual validation?
- Is it ready to merge?

Streams make those answers explicit.

---

## Core concepts

## Product task

A **product task** is a user-visible or operator-visible deliverable.

Examples:

- Add Events tab to the Agent panel
- Add Worklog tab to the Agent panel
- Keep Engineer Events countdown accurate
- Add inline task description editing

Product tasks answer:

> What outcome are we trying to ship?

These are the tasks that define product scope.

---

## Workflow task

A **workflow task** exists to move a stream safely.

Examples:

- Review implementation
- Fix review blockers
- Resolve merge conflict
- Validate after manual smoke

Workflow tasks answer:

> What operational step must happen so the stream can continue safely?

These are real tasks and remain fully auditable, but they are not the same thing as product scope.

---

## Visibility item

A **visibility item** is communication or orchestration context that the user should be able to see without treating it as product work.

Examples:

- a Engineer note to a worker
- a worker reply to the designated engineer
- a queue-control message such as “pause the queued task and fix the blocker first”

Visibility items answer:

> What context should the user be able to see in the stream timeline?

### Important rule

Visibility items should **not** behave like root/product tasks.

They should:

- be visible in history/timeline surfaces
- support reply/acknowledgement semantics
- stay out of backlog counts, queue ordering, and wave planning

In the current implementation, some visibility items may still be backed internally by task-shaped records for correlation and auditability, but the model treats them as **context**, not as product scope.

---

## Derived task

A **derived task** is a workflow-generated follow-up created from another task.

Examples:

- Review X
- Fix the issues found
- Resolve merge conflict
- Run validation after review

Derived tasks preserve:

- ownership handoff
- review lineage
- blocker/fix lineage
- explicit audit trails

Most derived tasks are **workflow tasks**.

---

## Stream

A **stream** is the branch/worktree-level execution and review lane.

It is the main continuity model for one reviewable slice of work.

A stream can include:

- multiple product tasks
- multiple workflow tasks
- zero or more visibility items
- one active mutable implementation lane
- queued future same-stream product work
- review/fix/re-review loops
- validation and merge gates

### The key idea

A stream answers:

> “What is the state of this branch/worktree slice right now?”

Not:

> “What is the state of one specific task?”

That distinction matters because one stream can legitimately contain several product tasks over time.

### Example

One stream might contain:

- Add Events tab
- Add Worklog tab
- Countdown fix
- Review Events implementation
- Fix blocker found in review
- Re-review blocker fix

That is still **one stream** if all of it lives on the same branch/worktree lane.

---

## Wave

A **wave** is the designated engineer’s scheduling decision.

It is the set of streams and/or standalone tasks that the designated engineer intentionally activates in parallel.

Waves answer:

> “What should be running at the same time right now?”

Examples of what a wave captures:

- current concurrency choice
- risk spread
- grouping by product surface
- whether to activate one risky stream plus two small ones, or just one deep stream

### Important distinction

A wave does **not** micromanage review blockers inside a branch.

That is stream behavior.

---

## The relationship between streams and waves

The relationship is:

```text
Wave
├── Stream A
│   ├── Product task
│   ├── Workflow task
│   └── Visibility items
├── Stream B
│   ├── Product task
│   └── Workflow task
└── Standalone task (optional)
```

So the clean mental model is:

- **waves** choose parallelism
- **streams** manage branch continuity
- **tasks** record work and handoffs
- **visibility items** show communication without pretending to be product work

---

## One important nuance: one mutable owner, not one total participant

A common simplification is:

> “Each stream has its own worktree, and only one task/agent at a time can work on it.”

That is almost right, but it needs one nuance.

### Correct version

Only **one task at a time should own the stream’s mutable implementation lane**.

That prevents conflicting writes and confusing queue behavior.

### But streams can still involve other participants

A stream may temporarily involve:

- an implementation agent owning the mutable branch
- a reviewer inspecting that branch
- a human validation gate
- a workflow fix task derived back to the parent implementation lane

So the invariant is:

> one foreground mutable owner at a time

not:

> only one task or agent may ever be associated with the stream

---

## Why stream state is not a field on one root task

This is one of the most important design choices.

If stream state lived on one root task, it would break as soon as one branch carried more than one product task.

Example:

- Product task A starts the branch
- Product task B is intentionally queued behind it on the same branch
- a later follow-up product task C lands on that same branch

There is still one branch/worktree execution lane, so there should still be one stream.

That is why:

- tasks can **belong to** a stream
- tasks can **display** stream information
- but the stream has its **own identity**

In the current implementation, stream identity is computed from:

- `repo_root`
- `branch`

and represented as:

- `stream_id`

---

## What the current stream model computes

Today’s implementation is a **computed read model**, not a persisted stream table.

Torque synthesizes stream state from existing data such as:

- branch/worktree boundaries
- successor tasks
- current agent ownership
- open review/fix tasks
- verification state
- worktree branch identity
- task classification

### Main files

- `torque/worktree_streams.py` — computed stream synthesis
- `torque/server_dispatch.py` — stream auto-resume behavior
- `torque/mcp_engineer.py` — Engineer stream tool exposure
- `static/js/engineer.js` — Open Streams UI

---

## Current stream fields

The exact payload may evolve, but the Phase 1/2 model includes fields like:

- `stream_id`
- `repo_root`
- `branch`
- `agent_id`
- `foreground_task_id`
- `product_task_ids`
- `workflow_task_ids`
- `queued_task_ids`
- `started_task_ids`
- `recent_visibility_items`
- `latest_boundary_task_id`
- `latest_reviewed_commit_sha`
- `active_review_task_id`
- `active_blocker_task_id`
- `state`
- `code_state`
- `validation_state`
- `merge_state`
- `queue_gate`
- `queue_items`
- `ready_to_resume_task_id`
- `can_auto_resume`
- `gate_reason`
- `recommended_next_action`

---

## Stream states

The current top-level stream states are:

- `implementing`
- `reviewing`
- `fixing_blockers`
- `awaiting_human_validation`
- `ready_to_merge`
- `merged`

These are stream states, not task-lane states.

### Supporting substate fields

To make the model more precise, Torque also computes:

#### `code_state`

Examples:

- `implementing`
- `reviewed_clean`
- `review_blocked`
- `merge_conflict`
- `merged`

#### `validation_state`

Examples:

- `none`
- `automated_only`
- `pending_human_validation`
- `validated`
- `waived`

#### `merge_state`

Examples:

- `not_ready`
- `ready`
- `merged`

---

## Queue behavior: now a stream feature

The queue is no longer just “whatever tasks happen to be queued on the same agent”.

Instead, it is:

> the ordered continuation plan for future product work inside a stream

That means the queue belongs to the stream’s state machine.

---

## Queue item states

Queued product tasks can now surface queue-specific states such as:

- `queued`
- `paused_by_blocker`
- `paused_by_review`
- `paused_by_validation`
- `held`
- `ready_to_resume`

These are **stream queue badges**, not board lanes.

---

## Queue gate

At most one stream queue gate should be active at a time.

Examples:

- `review_blocker`
- `merge_conflict`
- `human_validation`
- `manual_hold`

Each gate explains:

- what is currently blocking queued work
- which task caused the gate
- what has to happen for the gate to clear

Example shape:

```json
{
  "gate_type": "review_blocker",
  "blocking_task_id": "TORQUE:333:5",
  "source_task_id": "TORQUE:333:4",
  "reason": "Self-dispatch priming regression must be fixed before queued work resumes",
  "clears_when": "review_passes"
}
```

---

## Auto-resume

Phase 2 added stream-owned auto-resume behavior.

That means when a queue head becomes eligible again, Torque can automatically continue that stream’s next queued product task if the stream policy allows it.

### Current behavior

Torque now handles cases such as:

- the current blocker-fix loop clears and the next queued task becomes runnable
- dependencies clear
- another task in the same or a different group finishes and unblocks a queued stream head

This logic lives in:

- `torque/server_dispatch.py`

and uses the computed stream model rather than ad hoc queue rules.

---

## Review/fix loops

Streams also make review/fix/re-review loops easier to interpret.

Instead of treating every workflow task as an equal planning unit, the real meaning becomes:

- the stream has a current state
- the stream has one critical path task
- queued future product work may be paused behind that loop

### Current UI impact

The Phase 1 UI focuses on:

- surfacing open streams
- showing product, workflow, and visibility summaries separately
- exposing gate reason and next action

It does **not** yet fully collapse review/fix/re-review chains into a dedicated stream subview. That is still a future improvement.

---

## Validation as a first-class gate

Manual validation is now represented at the stream level.

That means a stream can clearly say:

- code is clean
- merge is still blocked
- the reason is human/runtime validation

### What is implemented today

The current model supports:

- `validation_state`
- `gate_reason`
- `recommended_next_action`
- `awaiting_human_validation`

### What is not fully implemented yet

The current model does **not** yet provide a full validation workflow engine with:

- dedicated validation approval flows
- validation-specific task controls
- validation waivers as a rich UI workflow

So validation is now **visible and structurally real**, but not yet fully operationalized.

---

## Open Streams in the Agent UI

The Agent panel now includes an **Open Streams** summary.

It is meant to answer, at a glance:

- which branches are open
- what state each stream is in
- what the current gate is
- what should happen next
- which product tasks, workflow tasks, and visibility/context items belong to the stream

This is the first user-facing surface of the stream model.

---

## Wireframes

## 1) Active wave with multiple streams

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Active Wave                                                         │
├──────────────────────────────────────────────────────────────────────┤
│ Stream A  Engineer Events + Worklog                                   │
│   state: fixing_blockers                                            │
│   foreground: Fix self-dispatch priming regression                  │
│   queue: Add Worklog tab (paused_by_blocker)                        │
│                                                                      │
│ Stream B  Interactive agent detail                                  │
│   state: implementing                                               │
│   foreground: Keep labels quick editor open                         │
│   queue: none                                                       │
│                                                                      │
│ Stream C  Self-dispatch prompt bug                                  │
│   state: reviewing                                                  │
│   foreground: Review self-dispatch prompt submission fix            │
│   queue: none                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

## 2) Single stream card

```text
┌───────────────────────────────────────────────────────────────┐
│ Stream: Engineer Events + Worklog                               │
├───────────────────────────────────────────────────────────────┤
│ Branch        torque/add-events-tab-to-the-engineer-p-837241c     │
│ State         Awaiting human validation                        │
│ Code          Reviewed clean                                   │
│ Validation    Pending manual smoke                             │
│ Merge         Not ready                                        │
│ Gate          Live/manual Engineer-panel smoke pending           │
│ Next action   Merge after validation                           │
│ Latest commit fbcf26b                                          │
│                                                               │
│ Product       TORQUE:333, TORQUE:334, TORQUE:342                    │
│ Workflow      review + blocker-fix lineage                    │
│ Context       Engineer reprioritized blocker fix                │
└───────────────────────────────────────────────────────────────┘
```

## 3) Blocker preempts queued work

```text
┌───────────────────────────────────────────────────────────────┐
│ Stream: Events + Worklog                                      │
├───────────────────────────────────────────────────────────────┤
│ State         Fixing blockers                                 │
│ Gate          Review blocker                                  │
│ Reason        Self-dispatch priming regression must be fixed  │
│                                                               │
│ Foreground    TORQUE:333:5  Fix review blockers                 │
│                                                               │
│ Queue                                                         │
│   1. TORQUE:334  Add Worklog tab          paused_by_blocker     │
│   2. TORQUE:342  Countdown update         queued                │
└───────────────────────────────────────────────────────────────┘
```

## 4) Gate clears and queue resumes

```text
Before review passes
--------------------
State: fixing_blockers
Queue head: TORQUE:334 paused_by_blocker

After blocker fix review passes
-------------------------------
State: implementing
Queue head: TORQUE:334 ready_to_resume
Action: Torque auto-dispatches TORQUE:334 if policy allows
```

---

## What is implemented today vs later

## Implemented now

### Phase 1

- computed stream synthesis
- product/workflow/visibility classification
- stream exposure through Engineer MCP and board summary surfaces
- Open Streams UI in the Agent panel
- validation represented as a stream gate

### Phase 2

- queue gate computation
- queue item states
- auto-resume behavior
- cross-group auto-resume fixes
- safer review-fix shell reuse in the auto-resume/review loop flow

---

## Still future work

The current model is intentionally incomplete in a few areas:

### Waves are still mostly a planning concept

Torque does not yet expose a first-class persisted wave object with dedicated history and UI.

Today, waves are still primarily:

- a Engineer planning concept
- a dispatch discipline
- something reflected in journal/checkpoint text

### Visibility items are still partly backed by task-shaped records internally

The important part is the presentation and semantics:

- they are treated as context
- not as product scope

There is still room to refine the storage model later.

### Review-loop compaction can improve further

The system now models streams much better, but the UI does not yet fully collapse all review/fix/re-review bookkeeping into one stream-native subview.

### Validation workflow is still partial

Validation is now first-class as a **state/gate**, but not yet as a fully operational workflow with dedicated controls and sign-off UX.

---

## Practical guidance for operators

When reading the Agent panel:

### Think in this order

1. **Which streams are open?**
2. **What state is each stream in?**
3. **What is the current gate?**
4. **What is the next action?**
5. **Which product task is foreground?**

Do not start by asking:

> “Which individual task card should I interpret first?”

That is exactly the confusion the stream model is meant to reduce.

### When a stream says `awaiting_human_validation`

Read that as:

> “The code path is likely clean, but merge is still intentionally blocked on manual validation.”

### When a queued task says `paused_by_blocker`

Read that as:

> “This is intentional. The stream has a current blocker-fix critical path.”

### When a visibility item appears

Read it as:

> “This is communication context for the stream, not a new product ask.”

---

## Related pages

- [Engineer](engineer.md)
- [Task Lifecycle](task-lifecycle.md)
- [Worktrees](worktrees.md)
- [Concepts](concepts.md)
- [Reference Guide](reference-guide.md)
- [Stream-Centered Orchestration Proposal](proposals/stream-centered-orchestration.md)
