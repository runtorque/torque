# Idea Brief backend contract (Wave A)

Idea Briefs are durable, proposal-only synthesis artifacts for product ideation. They are **not** executable tasks, accepted decisions, assignments, dispatches, merges, or deploy actions.

## Status lifecycle

- `draft` — editable working brief.
- `proposed` — explicitly marked ready for product-safe review.
- `parked` — durable but intentionally paused for later.
- `archived` — hidden by default; terminal visibility state.

`idea_brief_propose` / `architect_idea_brief_propose` only marks the brief proposed for review. The proposal payload records `auto_dispatch=false`, `auto_assign=false`, and empty `created_task_id` / `created_decision_id`.

## Durable fields

Each brief has:

- `id` (`<GROUP>-IB:<n>`), `slug`, `group_name` / `group`
- `title`
- `status`
- `problem_opportunity`
- `why_it_matters`
- `proposed_shape`
- `smallest_useful_version`
- `risks_tradeoffs`
- `open_questions`
- `thinking_links` (stable Thinking references)
- `source_context`
- `proposal`
- `refinement_log`
- actor/timestamp fields: `created_by_*`, `updated_by_*`, `parked_by_*`, `archived_by_*`, `created_at`, `updated_at`, `parked_at`, `archived_at`

## Thinking link payloads

`thinking_links` is a list of same-group Thinking references. Server and MCP wrappers validate that references exist, are not deleted, and belong to the brief's group.

Supported types:

```json
{"type":"scratchpad_note","id":"TORQUE-S:1","title":"...","slug":"...","group":"Torque","archived":false,"summary":"optional caller metadata"}
{"type":"mind_map","id":"TORQUE-M:1","title":"...","slug":"...","group":"Torque","node_count":2,"link_count":1}
{"type":"mind_map_node","id":"TORQUE-M:1:N1","map_id":"TORQUE-M:1","map_title":"...","label":"...","node_type":"..."}
{"type":"mind_map_link","id":"TORQUE-M:1:L1","map_id":"TORQUE-M:1","source_node_id":"...","target_node_id":"...","label":"...","link_type":"..."}
```

Caller-supplied metadata keys preserved when present: `context`, `source_context`, `summary`, `reason`, `quote`, `selection`, `note`, `confidence`.

## Trusted browser/server commands

All commands return errors as:

```json
{"type":"error","code":"validation_error|not_found|out_of_scope|archived|group_required|idea_brief_error","message":"...","contract":"torque.idea_brief.v1"}
```

Commands:

- `idea_brief_list`
  - request: `{cmd, group, status?, include_archived?, created_by_id?, limit?}`
  - response: `{type:"idea_brief_list", group, idea_briefs, contract, statuses, active_statuses}`
- `idea_brief_create`
  - request: `{cmd, group, title?, problem_opportunity, why_it_matters?, proposed_shape?, smallest_useful_version?, risks_tradeoffs?, open_questions?, thinking_links?, source_context?, actor_kind?, actor_id?}`
  - response: `{type:"idea_brief_created", idea_brief, ...contract}`
- `idea_brief_show`
  - request: `{cmd, group?, idea_brief|brief|brief_id|idea_brief_id|id, include_archived?}`
  - response: the brief row with `type:"idea_brief"`
- `idea_brief_update`
  - request: `{cmd, group?, idea_brief|..., field patches, thinking_links?, source_context?, status? (`draft`/`parked`; use `idea_brief_propose` for `proposed`), actor_kind?, actor_id?}`
  - response: `{type:"idea_brief_updated", idea_brief, ...contract}`
- `idea_brief_refine`
  - request: same patch fields as update plus `refinement_note?`
  - response: `{type:"idea_brief_refined", idea_brief, ...contract}`
- `idea_brief_park`
  - request: `{cmd, group?, idea_brief|..., reason?, actor_kind?, actor_id?}`
  - response: `{type:"idea_brief_parked", idea_brief, ...contract}`
- `idea_brief_archive`
  - request: `{cmd, group?, idea_brief|..., reason?, actor_kind?, actor_id?}`
  - response: `{type:"idea_brief_archived", idea_brief, ...contract}`
- `idea_brief_propose` (preferred) / `idea_brief_promote` (alias)
  - request: `{cmd, group?, idea_brief|..., note?|proposal_note?, review_target?, actor_kind?, actor_id?}`
  - response: `{type:"idea_brief_proposed", idea_brief, review_scope:"product_safe_review", proposal, caveat, ...contract}`

## WebSocket / snapshot state

Initial full and compact snapshots include:

```json
"idea_briefs": {"TORQUE-IB:1": { /* brief row */ }}
```

Deltas use one op:

```json
{"op":"idea_brief_upsert", "id":"TORQUE-IB:1", "group_name":"Torque", ...briefFields}
```

The current Wave A frontend only keeps `state.idea_briefs` fresh; Wave B owns the visible panel/list/detail UI.

## Proposal-safe MCP tools

Any Architect-derived class with the required Idea Brief capabilities can use:

- `architect_idea_brief_list`
- `architect_idea_brief_show`
- `architect_idea_brief_create`
- `architect_idea_brief_update`
- `architect_idea_brief_refine`
- `architect_idea_brief_park`
- `architect_idea_brief_archive`
- `architect_idea_brief_propose`

Tool rules:

- Group argument, when supplied, must match caller group.
- List/show are same-group reads and include `caller_owned`.
- Create writes caller-owned briefs only.
- Update/refine/park/archive/propose require caller ownership.
- Thinking links are validated same-group.
- Propose is review-only and never creates/dispatches/assigns work.
- Propose returns `review_scope:"product_safe_review"` plus the persisted `proposal` object.

Product peer/user context wrappers also accept `context_idea_brief_ids` and store them in `context_snapshot.proposal_context.idea_brief_ids`.

## Wave B UI smoke expectations

1. Load initial state and confirm `state.idea_briefs` exists in both full and compact snapshots.
2. Send `idea_brief_list` for active group and render draft/proposed/parked rows; archived rows appear only with `include_archived:true`.
3. Create a brief with all plain fields and at least one Scratchpad or Mind Map link; verify `idea_brief_upsert` updates `state.idea_briefs`.
4. Show a brief by id and slug; cross-group show returns `code:"out_of_scope"` or `not_found` in scoped wrappers.
5. Refine updates fields and appends `refinement_log` without losing Thinking links.
6. Park changes status to `parked`; archive hides it from default list.
7. Propose changes status to `proposed` and displays the caveat that no task/assignment/dispatch/accepted decision was created.
