# Implementation Plan: Auto-Generated Playbooks

**Status**: Proposed research/design
**Goal**: Learn reusable Loom workflows from successful historical task and pipeline runs, then publish them as reviewable playbook drafts that map cleanly onto existing actions, agent templates, and pipeline transitions.

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

---

## What A Playbook Is

A **playbook** is a reusable workflow recommendation describing how Loom should approach a recurring class of work.

It should include:

- a trigger or match rule for the work class
- the recommended entry action
- the recommended agent template
- the expected stage sequence or allowed branch points
- required runtime constraints such as worktree usage
- the human-review points that must remain in the loop
- evidence explaining why this pattern was suggested

A playbook is **not** a separate execution engine. At runtime Loom should still dispatch an action, use an agent template, and rely on action transitions for pipeline derivation.

The playbook layer is therefore a publishing and recommendation layer:

- **actions** answer "what prompt should each stage use?"
- **agent templates** answer "how should each stage be launched?"
- **pipelines** answer "what transitions are valid between stages?"
- **playbooks** answer "for this class of work, which action/template/pipeline combination has historically worked best?"

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

This object is for review and recommendation. Runtime dispatch still resolves through `entry_action`, the chosen template, and the declared transitions.

### Human review requirements

Before publication, a human should approve:

- the match scope
- the proposed action/template mapping
- the stage graph and any human gates
- whether a new playbook should create new action files, update existing ones, or stay as a recommendation only

Initial versions should require review for **every** published playbook. Auto-publishing should be out of scope until Loom has richer telemetry and a proven approval history.

### Publication targets

Publishing a playbook may result in one or more of:

- a new playbook metadata file used for recommendation
- a patch to existing action files
- a new agent template or a template recommendation
- new or updated action transitions to expose the learned pipeline
- a board or dispatch recommendation when the match rule fires

The important boundary is that playbooks do not replace actions or pipelines. They synthesize and organize them.

---

## Relationship To Actions, Templates, And Pipelines

The cleanest model is:

- **Actions** remain the stage-level authored prompts
- **Agent templates** remain the execution preset for a stage or playbook
- **Pipelines** remain the allowed graph of action transitions
- **Playbooks** become the learned entrypoint and workflow recommendation derived from history

This has two advantages:

1. Loom does not need a second orchestration engine.
2. Humans can inspect and edit the concrete runtime primitives they already understand.

In practice, a published playbook should usually point to:

- one preferred entry action
- one preferred template
- zero or more approved transitions already represented in action YAML
- optional guidance on when to stop, ask, or branch

---

## Follow-Up Implementation Tasks

1. **Persist stronger outcome signals**
   Add explicit run outcome, human review result, merge/PR result, and structured test summary fields to history.

2. **Build a historical run materializer**
   Add server-side code that reconstructs root-task chains into normalized run records.

3. **Add candidate-family scoring**
   Implement support/diversity/consistency scoring and ambiguity detection.

4. **Define playbook draft storage**
   Introduce a persisted draft format and storage location, likely alongside existing Loom project metadata rather than inside runtime state blobs.

5. **Create a review UI/CLI**
   Show evidence, counterexamples, and the concrete file changes a publication would make.

6. **Publish into existing primitives**
   Implement safe generation of action YAML updates, template recommendations, and transition patches from an approved playbook.

7. **Add recommendation-time matching**
   When a new task is created or dispatched, surface matching published playbooks as suggestions instead of silently forcing one.

8. **Backtest before enabling by default**
   Run the miner over existing Loom history fixtures and real projects to measure false positives and ambiguous families.

---

## Recommended Scope For A First Iteration

The first implementation should stay intentionally narrow:

- mine only from completed root-task chains
- cluster using root action plus conservative text normalization
- suggest only entry action, template, and simple linear stage sequences
- require human approval for every publication
- surface recommendations only in task creation/dispatch flows

That scope is enough to prove whether Loom can learn useful workflows from history without claiming more certainty than the data supports.
