# Task Lifecycle

Tasks in Loom move forward through lanes. They never bounce backward. This principle keeps the board readable at a glance --- you can look at the board and immediately understand where every piece of work stands without digging into history.

## Why forward-only

When a task bounces backward (e.g., from "In Review" back to "In Progress"), someone looking at the board has to ask *why* it moved back. The answer is buried in the task's history. The board stops being a snapshot and becomes a puzzle.

Loom avoids this by treating every task as a **discrete unit of work**. If an implementation fails review, the review agent doesn't reopen the original task --- it creates a new task describing what needs to be fixed. That fix task is its own unit of work, with its own lifecycle. The chain of tasks tells the full story: what was implemented, what the reviewer found, what was fixed, and what was approved.

## Lanes and status

The board has four fixed lanes. Tasks flow from left to right.

### Reserved lanes

All four default lanes are reserved --- they cannot be renamed or deleted:

| Lane | Purpose |
|------|---------|
| **Backlog** | Tasks waiting to be started. New tasks land here by default. `loom_ask(...)` creates tasks here for human review. |
| **To Do** | Planned tasks, prioritized and ready to be picked up. |
| **In Progress** | Active work. Tasks move here on dispatch and stay here until their pipeline chain resolves. |
| **Done** | Completed tasks. Reached via `loom_done(...)` or cascade completion. |

A task in **In Progress** means work is happening on it --- either directly by an agent, or indirectly through derived tasks in its pipeline chain. A task only leaves In Progress when its entire chain completes.

### Task status

While lanes track *where* a task is in its lifecycle, **status** tracks *what's happening* within a lane. When an agent derives a follow-up task, the parent task stays in In Progress but its status updates to reflect the current pipeline stage.

For example, a task might stay in In Progress the entire time, but its status changes from "Implementing" to "On Review" to "Fixing" to "On Review" to "Done."

Status is set by the `status` field on transitions:

```yaml
# implement.yaml
transitions:
  - action: review
    when: "Implementation complete, ready for code review"
    status: "On Review"
```

When the implementing agent calls `loom_derive(description="Review auth implementation", action="review")`, the parent task stays in In Progress and its status changes to **On Review**. If `status` is omitted, the status defaults to the target action's name (e.g., deriving to `review` sets status "review").

The board displays the status as a badge on the task card, giving you immediate visibility into what's happening without leaving the board view.

### Custom lanes

Users can still create additional lanes for manual organization (e.g., grouping tasks by team or priority). But the pipeline system never moves tasks to custom lanes --- it uses status within the fixed lanes instead. This keeps automated workflow and manual organization separate.

## Subordinate cards

Derived tasks appear on the board as **subordinate cards** --- smaller, indented cards nested under their parent task. This creates a visual hierarchy that shows the pipeline structure at a glance.

```
┌─────────────────────────────────┐
│ Add auth middleware              │
│ On Review · implement · Agent A  │
│                                  │
│   ┌─────────────────────────────┐│
│   │ Review auth implementation  ││
│   │ reviewing · Agent B         ││
│   └─────────────────────────────┘│
└─────────────────────────────────┘
```

The hierarchy is **collapsible** --- click the parent card to expand or collapse its children. When collapsed, you see just the parent card with its status badge. When expanded, you see the full chain of derived tasks.

If you click into a task, you see the full chain history: every derived task, its description, its outcome, and which agent handled it.

This means the board gives you two levels of detail:

- **Collapsed**: one card per unit of work, status badge shows current stage.
- **Expanded**: full pipeline chain with every step visible.

## Transitions and status

When an agent derives a follow-up task via `loom_derive(...)`, two things happen:

1. The **parent task** stays in In Progress. Its status updates to the value from the transition's `status` field.
2. A **new task** is created, dispatched to an agent, and shown as a subordinate card under the parent.

```yaml
# implement.yaml
transitions:
  - action: review
    when: "Implementation complete"
    status: "On Review"

# review.yaml
transitions:
  - action: fix
    when: "Issues found that need to be addressed"
    status: "Fixing"
```

The parent task's status reflects the current stage of its pipeline chain. As agents derive further tasks, the status updates automatically.

## Cascade completion

When a task is marked done via `loom_done(...)`, Loom walks up the parent chain and completes any ancestor tasks whose children are all finished. This is **cascade completion** --- the mechanism that moves tasks from In Progress to Done when their pipeline chain resolves.

### How it works

1. The agent calls `loom_done(message="summary")`. The current task moves to **Done**.
2. Loom looks at the parent task (via `parent_task_id`).
3. If the parent is already in Done, skip it and check the grandparent.
4. If the parent is NOT in Done, check: are **all** of this parent's derived tasks in Done?
5. If yes, move the parent to Done and continue up the chain.
6. If no, stop. The parent stays where it is.

Cascade only fires on `loom_done(...)` (and `loom_ready()`), never on derive. This prevents premature completion --- a review task that derives a fix task won't cascade the root task, because the fix task isn't done yet.

Some actions also opt into `auto_close_on_done`. That cleanup is evaluated at the **root-task lifecycle** level, not just when one derived task finishes. Loom only auto-closes an opted-in agent after the root task itself is **Done**, and it skips closure when unresolved descendants, queued same-agent follow-ups, or pending Engineer reply tasks still indicate expected follow-up work.

### Example: a review cycle

Consider an implement-review pipeline where the review finds issues on the first pass:

```yaml
# implement.yaml
transitions:
  - action: review
    when: "Implementation complete"
    status: "On Review"

# review.yaml
transitions:
  - action: fix
    when: "Issues found that need to be addressed"
    status: "Fixing"
```

Here's how the tasks flow:

**Step 1** --- Agent A implements the feature.

```
In Progress                        Done
┌──────────────────────────────┐
│ Add auth middleware           │
│ implementing · Agent A       │
└──────────────────────────────┘
```

**Step 2** --- Agent A calls `loom_derive(description="Review auth implementation", action="review")`. The task's status changes to "On Review". T2 (review) appears as a subordinate card.

```
In Progress                        Done
┌──────────────────────────────┐
│ Add auth middleware           │
│ On Review · implement         │
│                               │
│   ┌──────────────────────────┐│
│   │ Review auth impl.        ││
│   │ reviewing · Agent B      ││
│   └──────────────────────────┘│
└──────────────────────────────┘
```

**Step 3** --- Agent B reviews and finds issues. Calls `loom_derive(description="Fix null check in auth.py", action="fix")`. T2 stays in **In Progress** as the parent of the new fix task, its status changes to "Fixing", and T3 (fix) appears as a new subordinate card. The root task's status also changes to "Fixing".

```
In Progress                        Done
┌──────────────────────────────┐
│ Add auth middleware           │
│ Fixing · implement            │
│                               │
│   ┌──────────────────────────┐│
│   │ Review auth impl.       ││
│   │ Fixing · Agent B        ││
│   └──────────────────────────┘│
│   ┌──────────────────────────┐│
│   │ Fix null check           ││
│   │ fixing · Agent C         ││
│   └──────────────────────────┘│
└──────────────────────────────┘
```

**Step 4** --- Agent C fixes the issue. Calls `loom_derive(description="Re-review after fix", action="review")`. T3 stays in **In Progress** as the parent of the new review task, its status changes to "On Review", and T4 (review) appears.

```
In Progress                        Done
┌──────────────────────────────┐
│ Add auth middleware           │
│ On Review · implement         │
│                               │
│   ┌──────────────────────────┐│
│   │ Review auth impl.       ││
│   │ Fixing · Agent B        ││
│   └──────────────────────────┘│
│   ┌──────────────────────────┐│
│   │ Fix null check          ││
│   │ On Review · Agent C     ││
│   └──────────────────────────┘│
│   ┌──────────────────────────┐│
│   │ Re-review after fix      ││
│   │ reviewing · Agent D      ││
│   └──────────────────────────┘│
└──────────────────────────────┘
```

**Step 5** --- Agent D reviews and approves. Calls `loom_done(message="Approved, all tests pass")`. T4 moves to Done. **Cascade fires:**

- T4's parent is T3. T3 is already Done --- skip, continue up.
- T3's parent is T2. T2 is already Done --- skip, continue up.
- T2's parent is T1. T1 is in In Progress. T1's children: [T2]. T2 is Done. All children done --- **T1 moves to Done**.

```
In Progress                        Done
                                   ┌──────────────────────────────┐
                                   │ ✓ Add auth middleware        │
                                   └──────────────────────────────┘
```

The board told a clear story at every step. T1 was always visible with its current status, and only moved to Done when the entire chain resolved.

### Branching pipelines

Cascade handles branching correctly. If a task has multiple derived children (e.g., an agent spawns both a review task and a test task), the parent only cascades to Done when **all** branches complete.

```
┌──────────────────────────────┐
│ Add auth middleware           │
│ On Review · implement         │
│                               │
│   ┌──────────────────────────┐│
│   │ ✓ Review (approved)     ││
│   └──────────────────────────┘│
│   ┌──────────────────────────┐│
│   │ Run test suite           ││
│   │ testing · Agent E        ││
│   └──────────────────────────┘│
└──────────────────────────────┘

T1 stays in In Progress --- the test task isn't done yet.
```

When the test task completes, all children are Done and T1 cascades to Done.

### Non-cascading Engineer reply tasks

Not every follow-up task should advance the main implementation pipeline.

When the designated engineer sends a worker a question with `engineer_agent_message(...)`, Loom creates a visible follow-up task for that side conversation. The worker answers with `loom_reply(...)`, and that follow-up task closes with an **answered** outcome.

This completion is intentionally **non-cascading**:

- the reply task is marked done/answered
- the parent implementation or review task keeps whatever status it already had
- the root task does not move to **Done** just because the side conversation was answered

That keeps "please clarify this" threads visible on the board without accidentally resolving the main workflow before the real work is finished.

## Status propagation

When a derived task itself derives further (creating a chain deeper than one level), the **root task's status** updates to reflect the deepest active step. This way, you always know what's happening by looking at the root card.

```
T1 (implement) derives T2 (review)    → T1 status: "On Review"
T2 (review) derives T3 (fix)          → T1 status: "Fixing"
T3 (fix) derives T4 (review)          → T1 status: "On Review"
T4 (review) calls done                → T1 status cleared, T1 → Done
```

The root task's status is always driven by the most recent transition in its chain, regardless of depth. Intermediate tasks (T2, T3) update their own status too, but the root task is the one visible at the top level of the board.

## Task chain

Every derived task links back to its parent via `parent_task_id`, forming a chain. The chain is the audit trail --- it records what happened, in what order, and why.

```bash
loom task chain add-auth
```

```
depth 0  T1  implement  "Add auth middleware"           Done
depth 1  T2  review     "Review auth implementation"    Done
depth 2  T3  fix        "Fix null check in auth.py"     Done
depth 3  T4  review     "Re-review after fix"           Done
```

Click into any task on the board to see its full chain. The chain combined with status gives you two views of the same work:

- **The board** shows where things are *right now* --- status badges on cards in fixed lanes.
- **The chain** shows how things *got there* --- the full derivation history.
