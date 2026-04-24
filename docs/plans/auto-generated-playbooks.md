# Implementation Plan: Auto-Generated Playbooks

**Status**: Proposed research/design
**Goal**: Learn reusable Loom workflows from successful historical task and pipeline runs, then publish them as reviewable playbook drafts that can become explicit, human-approved operational artifacts built from existing Loom actions, agent templates, and pipeline transitions.

---

## The Problem

Loom already captures enough structure to see repeated workflows:

- board tasks record action names, labels, timestamps, parent/child pipeline links, and task activity messages
- agent history records template, type, task count, and message history
- worktrees preserve branch identity and checkpoint history

That is enough to observe that some patterns recur: `feature/implement -> feature/review`, research tasks that always escalate to a human gate, or work that reliably succeeds only when a specific template or worktree mode is used.

What Loom does **not** have today is a safe way to turn that history into a reusable artifact. Naively copying the most common sequence would produce brittle cargo-cult automation:

- one-off firefights look like workflows if you only inspect a few runs
- repeated failures can masquerade as "activity"
- current history captures outcomes and messages, but not full reasoning transcripts or structured test results

The design therefore has to answer two questions at once:

1. What counts as a playbook candidate?
2. How do we prevent Loom from publishing false patterns?

It also needs a third answer for v1 that earlier versions left too vague:

3. Who actually uses a playbook, and when does it become usable?

---

## Design Principles

1. **Learn structure, not transcripts**

   The system should mine stable workflow structure: entry conditions, action sequence, template choice, worktree requirement, and human-review gates. It should not synthesize a playbook by copying raw agent prose from prior runs.

2. **Use the pipeline chain as the primary unit**

   The main observation is not an individual task. It is a root task plus its derived descendants, because successful Loom workflows often span multiple actions.

3. **Publish suggestions, not silent mutations**

   A mined playbook should become a draft artifact with evidence attached. It should not automatically rewrite actions or enable a new pipeline without review.

4. **Map onto Loom's existing primitives**

   Actions remain the executable prompt units. Agent templates remain launch/runtime presets. Pipelines remain emergent from action transitions. Playbooks sit above them as a recommendation and packaging layer.

5. **Bias against false positives**

   If the evidence is sparse, noisy, or contradictory, the output should be "no candidate" rather than a weak playbook.

6. **Separate drafts from operational objects**

   Unpublished candidates are inert review artifacts. Only published playbooks become usable Loom objects.

---

## Consumer And Activation Path

The primary v1 consumer is a **human workflow curator**:

- a project maintainer
- an operator managing Loom actions/templates
- a human reviewing project automation before it is exposed to day-to-day users

The activation path is explicit:

1. Loom mines history and creates a **draft playbook candidate**.
2. A human reviews the candidate, its evidence, and the exact publication effect.
3. If approved, Loom publishes a **playbook recipe**.
4. Only that published recipe is exposed operationally in Loom.

For v1, a published playbook should be a named **dispatch recipe**:

- it defines when the playbook applies
- it points to the entry action and preferred agent template
- it records required constraints such as worktree usage and required human gates
- it may also generate or validate the action-transition bundle that supports the expected workflow

That makes the consumer and the activation path concrete:

- **draft candidates** are used only by the human reviewer
- **published playbook recipes** are used by Loom operators in task creation/dispatch flows

The designated engineer is **not** a v1 consumer of raw generated candidates. A later phase may let Engineer recommend or execute **published** playbooks, but unpublished drafts must remain inert.

---

## What A Playbook Is

A **playbook** is a reusable workflow artifact describing how Loom should approach a recurring class of work.

It should include:

- a trigger or match rule for the work class
- the recommended entry action
- the recommended agent template
- the expected stage sequence or allowed branch points
- required runtime constraints such as worktree usage
- the human-review points that must remain in the loop
- evidence explaining why this pattern was suggested

A draft playbook candidate is **not** operational by itself. It becomes operational only after publication.

For v1, the operational form should be a **published dispatch recipe** that references existing Loom primitives. A published playbook is **not** a separate execution engine. At runtime Loom should still dispatch an action, use an agent template, and rely on action transitions for pipeline derivation.

The playbook layer is therefore a publishing and packaging layer:

- **actions** answer "what prompt should each stage use?"
- **agent templates** answer "how should each stage be launched?"
- **pipelines** answer "what transitions are valid between stages?"
- **draft playbook candidates** answer "what historically worked, and should a human publish it?"
- **published playbooks** answer "which approved action/template/pipeline bundle should an operator reuse for this class of work?"

---

## Extraction Model

### 1. Build the historical run dataset

The extraction job should materialize one **historical run** per root task.

Each run contains:

- root task metadata: title, labels, group, action, action vars, timestamps
- full chain: ordered descendant tasks with `parent_task_id`, `pipeline_depth`, action, lane, status, labels, and messages
- execution metadata per stage: agent template, agent type, worktree usage, branch identity, checkpoints
- terminal outcome signals: done, blocked, error, ask, human handoff, merge/PR result when available

This is the core observation model:

- **Stage**: one task executed under one action/template pairing
- **Run**: one root task plus all derived stages
- **Family**: similar runs grouped together for candidate mining

### 2. Normalize runs into comparable signatures

Each run should be converted into three signatures:

- **Intent signature**: what kind of work this appears to be
  - initial action
  - stable labels
  - normalized task text
  - optional future semantic cluster id
- **Workflow signature**: how the work moved
  - ordered action sequence
  - `ask` or human gates
  - whether the run stayed single-stage or became a pipeline
- **Execution signature**: how it was launched
  - agent template
  - agent type/provider
  - worktree on/off
  - optional future model/tooling settings

Phase 1 should keep normalization conservative:

- strip ticket numbers, file paths, SHAs, and quoted literals from task text
- bucket by root action plus normalized labels
- treat obviously unique runs as outliers instead of forcing them into a family

Phase 2 can add semantic clustering once Loom has better transcript and artifact capture.

### 3. Form candidate families

A family is a group of runs that share:

- the same root action
- similar normalized labels and task text
- a dominant workflow signature
- a dominant execution signature

The candidate playbook is derived from the dominant pattern inside the family, not from a single run.

### 4. Score candidate playbooks

A family qualifies as a playbook candidate only if it passes all of these default gates:

- **Support**: at least 3 successful root runs
- **Diversity**: evidence spans at least 2 separate days and 2 distinct normalized task titles
- **Consistency**: one workflow signature accounts for at least 80% of successful runs in the family
- **Execution stability**: one template/worktree combination accounts for at least 70% of successful runs
- **Advantage**: the dominant pattern performs materially better than the family's alternatives on completion and escalation rate

These thresholds should stay configurable, but the default posture should be skeptical.

### 5. Produce a draft playbook, not a live change

The extractor should emit a draft object with:

- proposed playbook metadata
- supporting evidence
- counterexamples that were excluded
- confidence score
- generated follow-up patches or suggestions for actions/templates/transitions

If the family does not pass the gates, the output should be an internal observation only.

If the family does pass the gates, the output is still only a **draft candidate**. It does not become visible as a reusable Loom object until a human publishes it.

---

## Success Signals

Success should be measured at the **run** level first, then rolled up to the family.

### Strong signals

- the terminal task in the chain ends in `Done`
- no stage in the chain ends in `blocked` or `error`
- a review stage ends without spawning a subsequent fix stage
- the chain reaches its expected terminal action without exceeding depth limits
- the worktree is later merged or a PR is created successfully, if Loom has that signal

### Medium signals

- positive `done` messages with concrete completion summaries
- low ratio of `ask` escalations for the family
- low retry/fix churn relative to similar runs
- stable completion time compared with the family baseline

### Negative signals

- unresolved `ask` tasks
- tasks marked with `loom:error` or `loom:blocked`
- review chains that repeatedly bounce between review and fix
- runs that required manual rescue outside the inferred pattern
- abandoned chains where the latest descendant never completed

### What should not count as success by itself

- a high volume of agent messages
- a large number of checkpoints or commits
- the mere existence of a repeated sequence
- a task ending in `Done` when a later human note or child task indicates the work still failed

---

## Historical Data Requirements

The proposal should explicitly separate what Loom already records from what must be added for robust extraction.

| Signal | Available today | Use | Gap |
|---|---|---|---|
| Task chain structure (`parent_task_id`, `pipeline_depth`, `pipeline_root_id`) | Yes | Reconstruct runs and stage order | None |
| Task actions, labels, timestamps, lane | Yes | Build intent/workflow signatures | None |
| Task activity messages (`progress`, `done`, `blocked`, `error`) | Yes | Infer stage outcome and summaries | Message semantics are free-form |
| Agent template and agent type | Yes | Build execution signature | Template choice is recorded per agent, not versioned |
| Worktree branch and checkpoints | Yes | Detect shared worktree workflows | Checkpoints do not encode test/merge outcomes |
| PR creation / merge result | Partial | Strong terminal success signal | Needs consistent persistence |
| Human review/approval decision | Partial | Distinguish approval from mere completion | Needs explicit approval outcome field |
| Test commands and pass/fail artifacts | No | Determine whether a pattern is genuinely successful | Needs structured result capture |
| Session transcript and tool trace | No durable store | Later semantic clustering and failure analysis | Needs session recording |
| Normalized task family tags | No | Better clustering than lexical matching | Needs classifier or manual tagging |

### Minimum viable data for v1

V1 can ship using existing task-chain history plus a few narrow additions:

- explicit run outcome on terminal completion
- explicit human review outcome for `ask` and review gates
- persisted merge/PR success
- optional structured test-result summary attached to a task

Full transcript mining should be deferred until session recording exists.

---

## False Pattern Detection

This is the main safety problem, so the rules should be explicit.

1. **Mine families, not anecdotes**

   A single impressive run never produces a publishable playbook.

2. **Deduplicate retries**

   Multiple retries of the same root task should count as one troubled run, not as many examples supporting the same pattern.

3. **Prefer stable graph shapes**

   If a family alternates between several incompatible action sequences, the result is ambiguous and should not publish.

4. **Discount emergency work**

   Runs with repeated `blocked`, `error`, or manual intervention should lower confidence even if they eventually end in `Done`.

5. **Ignore overly specific tokens**

   File names, issue IDs, commit SHAs, branch names, and one-off literals should not become match criteria or prompt content.

6. **Require a better-than-baseline outcome**

   A frequent pattern that performs no better than the family's alternatives is not a useful playbook.

7. **Show counterevidence to the reviewer**

   Every draft should list near-miss runs and contradictory workflows so the human reviewer can reject overfit candidates quickly.

---

## Publication Model

Playbooks should be published through a review workflow with clear outputs.

### Draft artifact

The extractor creates a draft playbook object such as:

```yaml
name: feature-safe-implementation
match:
  root_action: feature/implement
  labels: [feature]
  normalized_task_family: add-or-change-product-behavior
entry_action: feature/implement
agent_template: default
workflow:
  - action: feature/implement
  - action: feature/review
  - ask: true
constraints:
  worktree: true
evidence:
  successful_runs: 6
  total_runs: 7
  dominant_workflow_share: 0.86
  dominant_execution_share: 0.83
review_required: true
```

This object is for review only. Runtime dispatch does not consume it directly. Runtime dispatch still resolves through `entry_action`, the chosen template, and the declared transitions, but only after a human publishes a corresponding operational playbook recipe.

### Human review requirements

Before publication, a human should approve:

- the match scope
- the proposed action/template mapping
- the stage graph and any human gates
- whether publication should create or update the operational recipe and any supporting action/transition artifacts

Initial versions should require review for **every** published playbook. Auto-publishing should be out of scope until Loom has richer telemetry and a proven approval history.

### Publication targets

Publishing a playbook should produce a concrete operational object. For v1, that object should be a **published dispatch recipe** stored in project metadata and exposed intentionally in Loom's dispatch/task-creation flows.

Publication may also result in one or more supporting changes:

- a new published playbook recipe
- a patch to existing action files
- a new agent template or a template recommendation
- new or updated action transitions to expose the learned pipeline
- optional validation that the referenced action bundle exists and matches the approved workflow

The important boundary is:

- draft candidates are not operational
- published playbooks are explicit operator-facing recipes
- playbooks do not replace actions or pipelines; they package and organize them

---

## Relationship To Actions, Templates, And Pipelines

The cleanest model is:

- **Actions** remain the stage-level authored prompts
- **Agent templates** remain the execution preset for a stage or playbook
- **Pipelines** remain the allowed graph of action transitions
- **Draft playbook candidates** become the mined review artifacts derived from history
- **Published playbooks** become the approved dispatch recipes that bind the learned workflow to Loom's runtime primitives

This has two advantages:

1. Loom does not need a second orchestration engine.
2. Humans can inspect and edit the concrete runtime primitives they already understand.

In practice, a published playbook should usually point to:

- one preferred entry action
- one preferred template
- zero or more approved transitions already represented in action YAML
- optional guidance on when to stop, ask, or branch
- a clear operator-facing activation point in task creation or dispatch

### Engineer integration

Engineer integration should be deferred until after publication exists.

If added later, Engineer may:

- recommend a **published** playbook when planning work
- choose a **published** playbook as part of an explicit orchestration strategy

Engineer should not read or act on unpublished candidate drafts.

---

## Follow-Up Implementation Tasks

### Task 1: Historical pattern extraction

1. **Persist stronger outcome signals**
   Add explicit run outcome, human review result, merge/PR result, and structured test summary fields to history.

2. **Build a historical run materializer**
   Add server-side code that reconstructs root-task chains into normalized run records.

3. **Add candidate-family scoring**
   Implement support/diversity/consistency scoring and ambiguity detection.

4. **Store inert draft candidates**
   Introduce persisted draft-candidate storage with evidence and counterexamples, but no runtime activation.

### Task 2: Draft playbook generation and explicit human publication

5. **Create a review UI/CLI**
   Show evidence, counterexamples, and the exact publication effect for a candidate.

6. **Define the published operational object**
   Add a published playbook recipe format for task-creation/dispatch flows. This is the v1 consumer-facing artifact.

7. **Publish into existing primitives**
   Implement safe generation or validation of the referenced action/template/transition bundle from an approved playbook.

8. **Surface only published playbooks operationally**
   Expose published recipes in task creation/dispatch flows. Do not surface raw candidates to Engineer or normal operators.

9. **Backtest before enabling by default**
   Run the miner over existing Loom history fixtures and real projects to measure false positives and ambiguous families.

---

## Recommended Scope For A First Iteration

The first implementation should stay intentionally narrow:

- mine only from completed root-task chains
- cluster using root action plus conservative text normalization
- produce only inert draft candidates from the mining step
- publish only explicit operator-facing dispatch recipes
- suggest only entry action, template, and simple linear stage sequences
- require human approval for every publication
- surface only **published** playbooks in task creation/dispatch flows
- keep Engineer out of the loop until a later published-playbook integration step

That scope is enough to prove whether Loom can learn useful workflows from history without claiming more certainty than the data supports.
