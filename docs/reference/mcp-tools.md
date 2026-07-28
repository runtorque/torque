# MCP tools reference

Torque exposes one canonical MCP vocabulary. Tool names describe an operation,
not the kind of agent calling it. For example, both Architects and Engineers
use `peer_message`; Torque derives peer eligibility and dispatches to the
appropriate internal implementation from the authenticated caller.

Provider wrappers may add their own server prefix. This document uses the
names returned by MCP `tools/list`.

## Contract

- `tools/list` returns canonical names only. Historical `torque_*`,
  `engineer_*`, and `architect_*` names are hidden compatibility aliases.
- Caller identity, frozen Agent Class authority, capability scope, ownership,
  and relationship checks determine which operations are visible and callable.
- Common operations keep the same name across Workers, Engineers, Architects,
  and proposal-only Agent Classes.
- An operation can select different semantics without changing its public
  name. A Product Manager's `task_create`, for example, creates a non-binding
  proposal; a full Architect's `task_create` creates an executable task.
- The model-context surface is intentionally bounded. `tools/list` still
  advertises every authority-projected callable operation so provider-native
  tool search can retain a real MCP handler while deferring schemas from the
  prompt.
- Internal authority metadata and compatibility aliases are never returned in
  public tool schemas.

### Argument receipts and canonical translation audit

Torque tolerates undeclared arguments rather than rejecting them. This is a
deliberate compatibility decision: MCP clients may send forward-compatible
extra fields, and rejecting those fields would turn a diagnostic improvement
into a client-breaking change. Every successful or failed exact public-tool
call that includes one now gets a caller-visible receipt such as
`Undeclared parameter received: agent_id. They are not part of this public
tool schema.` Tool-result responses carry it as a second text block;
pre-dispatch JSON-RPC errors (including authorization, tombstone, and
idempotency exits) append it to the error message. The operation's acceptance
and handler semantics are otherwise unchanged; callers must not treat a
successful call as evidence that an extra field is declared.

The public argument for an Architect's single-agent reference is `agent`.
This includes `agent_message`, `behavior_overlay_get`,
`behavior_overlay_versions`, `behavior_overlay_diff`,
`behavior_overlay_proposal_list`, and `telemetry_query`. Their handler fields
remain `engineer_id` or `agent_id` internally. `behavior_overlay_propose` and
`behavior_overlay_rollback` instead use the deliberate polymorphic
`target`/`target_kind` pair because they can address either an agent or a
role; they are not alternate single-agent spellings.

Audit of `translate_canonical_arguments`:

- **Leaks fixed:** `peer` → `architect_id`/`engineer_id` now reports `peer`;
  `agent` → `engineer_id` reports `agent`; `supervisor` → `architect_id`
  reports `supervisor`; and batch `entries` → `tasks` reports `entries`.
- **Checked clean:** `peer` → optional
  `peer_architect_id`/`peer_engineer_id`; `help_search` mode-only key removal;
  `area_link`, `initiative_link`, and behavior-overlay target routing;
  `memory_set_pin`, event delivery, lifecycle, and hire-list mode selection;
  thinking/scratchpad/mind-map aliases; and behavior-review compatibility
  fields. These paths either use their public name in validation or have no
  required-target error after translation.

This audit concerns caller-facing diagnostics only. It does not alter
authorization, scope, persistence, or audit-record field names.

### Authorization refusals and positive controls

For an **exact canonical name already present in the caller's `tools/list`**,
Torque identifies the authorization layer that refused the call: the operation
is not projected for the caller, the session's frozen Agent Class authority
snapshot denies it (relaunch after an approved class change), or the requested
target is outside the caller's authorized scope. The final form intentionally
does not distinguish an absent target from an out-of-scope target. Unknown
names, legacy aliases, and canonical names absent from `tools/list` still
return only `Unknown tool`; a refusal must never become a discovery oracle.

When diagnosing a remaining ambiguous handler-level response, use a
same-session positive control only when it resolves to the **same internal
handler**: call the same public tool against a target that should be allowed.
A handler-level response (including a non-authorization error) proves that
tool projection and frozen authority passed for that handler in that session;
a refusal on the original target is then target-specific. This control is not
available for argument-dependent public-name resolution, because different
arguments select a different handler and identical arguments are the original
call. Read source instead for: `event_delivery_update`,
`engineer_lifecycle`, `hire_list`, `idea_brief_transition`,
`idea_brief_update`, `thinking_list`, and `thinking_get`.

| Public-name resolution | Diagnostic route |
| --- | --- |
| Argument-independent / same handler | Same-session positive control can eliminate projection and frozen-authority causes. |
| Argument-dependent | No external positive control exists; inspect the selected handler and authorization path. |

## Discovery

`tool_search(query, max_results)` searches only operations Torque classifies
for on-demand use and that are already allowed by the caller's effective
authority. Use `select:<tool_name>` for an exact schema. It is a compact
portable schema lookup, not an activation endpoint: deferred operations are
already registered from `tools/list`, and provider-native tool search decides
when to load them into model context.

Workers have no deferred catalog. Engineers and Architects have a bounded
eager subset plus an authority-filtered on-demand subset; both subsets are
present in `tools/list`. Torque emits the eager partition first, in stable
source order, before the deferred partition so provider-native discovery sees
the bounded boot core before indexing specialty schemas.

### Engineer eager core

For a fresh full-authority hired Engineer, the maintained eager budget is
exactly 57 canonical operations. A restricted Engineer receives only the
intersection of this set and its frozen Agent Class authority, and
relationship-specific operations such as `supervisor_message` are projected
only when the caller has the required relationship:

| Category | Eager canonical operations |
| --- | --- |
| Boot and orientation | `context`, `tool_search`, `help_query`, `help_get`, `board_summary`, `boot_summary`, `session_map`, `event_list` |
| Communication, task, and agent routing | `task_list`, `task_get`, `task_create`, `task_update`, `task_move`, `task_dispatch`, `task_verify`, `task_artifact_upload`, `agent_list`, `agent_get`, `agent_message`, `agent_ask_answer`, `raise`, `user_message`, `user_note`, `peer_list`, `peer_message`, `peer_inbox`, `peer_reply`, `supervisor_message`, `agent_reply` |
| Memory and context | `memory_publish`, `memory_list`, `memory_get`, `memory_set_pin`, `memory_link`, `semantic_recall`, `journal_write`, `journal_list` |
| Execution flow | `stream_list`, `stream_get`, `action_list`, `action_get`, `hint_set_state`, `event_delivery_update` |
| Worktree and release | `worktree_diff`, `worktree_checkpoint`, `worktree_advance_boundary`, `worktree_rebase`, `worktree_create_pr`, `worktree_merge`, `worktree_remove`, `worktree_adopt` |
| Task and agent operations | `task_mark_covered`, `task_coverage_reconcile` (**NOT YET AVAILABLE** until TORQUE:1228 is merged and the caller session is relaunched), `task_reassign`, `agent_launch_settings`, `agent_close`, `agent_relaunch` |

Eager classification changes boot availability, not authorization or handler
semantics. Frozen Agent Class capability checks still filter `tools/list`, and
the existing ownership, worker/peer visibility, Architect-chain
communication, worktree boundary, reviewed-SHA, and merge gates still apply
at call time. Because `tool_search` searches the deferred partition only, an
exact search for an authorized eager operation returns no deferred schema;
the operation is already present in the initial projection. Denied operations
are absent from both the projection and exact search.

Intentionally lazy Engineer exceptions include unrelated planning,
specialization editing, behavior-overlay administration, deep telemetry, and
other specialty inspection operations. External connectors are separate from
this Torque projection and remain lazy/provider-managed.

### Architect eager core

For a full-authority Architect, the maintained eager budget is exactly 54
canonical operations. A restricted Architect receives only the intersection
of this set and its frozen Agent Class authority:

| Category | Eager canonical operations |
| --- | --- |
| Boot and orientation | `context`, `tool_search`, `help_query`, `help_get`, `board_summary`, `boot_summary`, `event_list`, `agent_list`, `area_list`, `area_get`, `initiative_list`, `initiative_get` |
| Communication | `raise`, `user_message`, `peer_list`, `peer_message`, `peer_inbox`, `peer_reply`, `agent_message`, `agent_reply`, `agent_ask_get`, `agent_ask_answer` |
| Task routing and evidence | `task_list`, `task_get`, `task_chain`, `task_claim`, `task_create`, `task_update`, `task_reassign`, `task_move`, `task_mark_covered`, `task_coverage_reconcile` (**NOT YET AVAILABLE** until TORQUE:1228 is merged and the caller session is relaunched), `task_verify` |
| Durable context | `journal_write`, `journal_list`, `decision_list`, `decision_get`, `decision_create`, `decision_update`, `decision_link`, `memory_publish`, `memory_list`, `memory_get`, `memory_set_pin`, `memory_link`, `semantic_recall` |
| Execution flow | `attention_digest`, `group_health_brief`, `wave_summary`, `completion_audit`, `worktree_merge`, `worktree_rebase`, `worktree_create_pr`, `worktree_diff` |

`task_create` is the Architect's dispatch-capable routing flow: when its
authorized dispatch arguments are used, it routes to a hired Engineer.
Architects do not receive Worker dispatch authority.

Intentionally lazy exceptions are unrelated administration and lifecycle
operations, behavior overlays, telemetry/settings/deploy inspection, broad
event-delivery configuration, deep planning mutations, Idea Brief/Thinking
editing, Engineer feedback/thread inspection, and backlog reconciliation.
They remain discoverable when authorized. External connectors are separate
from this Torque projection and remain lazy/provider-managed.

## Shared vocabulary

### Context and documentation

| Tool | Purpose |
| --- | --- |
| `context` | Read caller identity, assignment, worktree, and task context. |
| `help_search` | List or search maintained Torque Help documentation. |
| `help_query` | Answer a question from maintained Help with source excerpts. |
| `help_get` | Read one Help topic or section. |
| `tool_search` | Discover an authority-filtered deferred tool schema. |
| `boot_summary` | Read cached recovery context without a live provider call. |
| `semantic_recall` | Search AI-indexed context within existing visibility rules. |

### Semantic recall

`semantic_recall(query, limit)` searches the configured local AI index and
re-applies the caller's ordinary task, journal, decision, and peer-context
visibility rules to every result.

Per-call corpus narrowing is not part of the v1 MCP schema. Corpus selection is
configured in Settings → AI for the local index as a whole; caller visibility
is still enforced again at read time.

Successful reads return ranked snippets with their source type, visible source
identifier, title, score, and update time. Disabled, missing-dependency,
not-ready, rebuild-pending, and model-mismatch states return an empty result
with fallback guidance; they do not block orchestration.

### Cached boot summaries

`boot_summary` returns a cached boot-recovery summary. Reads never schedule or
perform a live provider call; generation and refresh happen out of band after
AI is explicitly enabled.

Payload fields include:

| Field | Meaning |
| --- | --- |
| `type` | Caller-specific boot-summary type. |
| `summary_key` | Stable cache key for the caller scope. |
| `status` | Summary readiness. Treat anything except `ready` as a raw-tool fallback. |
| `summary` | Cached text summary, empty when unavailable. |
| `source_counts` | Redacted source counts used to build the cache row. |
| `generated_at` | Unix timestamp for the cached row. |
| `source_hash` | Content hash for freshness comparison. |
| `message` | Human-readable fallback guidance or error text. |

Readiness statuses are:

| Status | Meaning |
| --- | --- |
| `ready` | Cached summary is current for the recorded source hash/provider/model. |
| `stale` | A previous summary exists, but raw deterministic tools are authoritative. |
| `empty` | No usable cached summary exists yet. |
| `refreshing` | An out-of-band refresh is in progress; use raw recovery tools. |
| `error` | The last refresh failed; use raw recovery tools. |

When AI or cached boot summaries are disabled, the MCP read returns
`status: "empty"` with a disabled/fallback message. Treat `empty` and
`refreshing` the same as `stale`: continue with raw deterministic tools and
never block boot, dispatch, review, or merge on the summary.

### Human and agent communication

| Tool | Purpose |
| --- | --- |
| `user_message` | Send a durable non-blocking message to the user. |
| `raise` | Raise a blocking decision or approval to the immediate decision owner. Workers route to their owning Engineer, hired Engineers to their hiring Architect, and only Architects/orphans fall through to the user. |
| `user_note` | Record a non-blocking note or soft question. |
| `user_message_loop_stop` | Stop the caller's direct-message loop. |
| `peer_list` | List eligible same-level peers. |
| `peer_message` | Message an eligible peer. Engineers and Architects share this name. |
| `peer_inbox` | Read durable same-level peer threads. |
| `peer_reply` | Reply to a same-level peer thread. |
| `peer_context` | Inspect context explicitly attached to a peer thread. |
| `agent_message` | Message an eligible subordinate agent. |
| `supervisor_message` | Message the caller's supervising agent. |
| `agent_reply` | Reply in a supervisor/subordinate thread. |
| `agent_thread_list` | List visible subordinate peer threads. |
| `agent_thread_get` | Inspect one visible subordinate peer thread. |
| `agent_ask_get` | Read a subordinate's pending blocking question. |
| `agent_ask_answer` | Answer or resolve a subordinate question. |

`peer_message` takes `peer` and `message`. Optional context fields depend on
the caller. Setting `ack_required` additionally requires
`message.ack_request`; ordinary peer messaging does not grant that authority.

`agent_message` and `supervisor_message` express relationship direction.
Recipient kind is not part of the capability model.

`user_message` uses explicit reply threading. A `## Message from the User`
injection includes its Message ID; pass that value as `reply_to_id` when
replying. Omit `reply_to_id` for a proactive status or introduction. Torque
does not guess a reply target from historical conversation rows.

### Tasks

| Tool | Purpose |
| --- | --- |
| `board_summary` | Read a compact board/orchestration summary. |
| `task_list` | List visible tasks. |
| `task_get` | Read one visible task. |
| `task_chain` | Walk a task's derivation chain. |
| `task_create` | Create an executable task or proposal according to authority. |
| `task_update` | Patch writable task fields. |
| `task_claim` | Claim an eligible routed task. |
| `task_reassign` | Change task ownership within the caller's ceiling. |
| `task_move` | Move a task between lanes. |
| `task_dispatch` | Dispatch one task or an ordered batch. |
| `task_mark_covered` | Record review/root-task coverage. |
| `task_coverage_reconcile` | **NOT YET AVAILABLE** recognized strict reconciliation route; no mutation until **TORQUE:1228 is merged and the caller session is relaunched** (projection/authority are frozen per session). |
| `proposal_root_backlog_hygiene` | Inventory covered product-proposal roots and optionally finalize eligible roots. |
| `task_artifact_upload` | Attach an artifact to a visible task. |
| `task_verify` | Record tests, deploy, restart, and smoke evidence. |
| `task_progress` | Report current Worker activity. |
| `task_complete` | Complete a Worker's linked task. |
| `task_blocked` | Mark a Worker's task blocked. |
| `task_error` | Record an unrecoverable Worker error. |
| `task_derive` | Create an allowed pipeline successor. |
| `agent_ready` | Complete work and release the Worker. |

`task_dispatch` replaces separate single and batch tools:

- Pass `task` for a single dispatch.
- Pass `entries` for an ordered batch.
- Each entry contains `task` and may contain `agent_group` for same-agent
  affinity.
- `max_concurrent` controls active batch concurrency.

### Inventory and control

| Tool | Purpose |
| --- | --- |
| `agent_list` / `agent_get` | List or inspect visible agents. |
| `agent_rename` | Suggest a better Worker name. |
| `agent_close` / `agent_relaunch` | Control eligible Worker sessions. |
| `agent_launch_settings` | Inspect or update eligible launch settings. |
| `action_list` / `action_get` | Discover Action definitions. |
| `specialization_list` / `specialization_get` | Discover Engineer specialization definitions. |
| `specialization_save` / `specialization_delete` | Manage writable specialization definitions. |
| `stream_list` / `stream_get` | Inspect active orchestration streams. |
| `session_map` | Read deterministic Engineer recovery/orientation state. |
| `hint_set_state` | Snooze or update an eligible runtime hint. |

### Memory and journal

| Tool | Purpose |
| --- | --- |
| `memory_publish` | Publish shared context. |
| `memory_list` / `memory_get` | List or read visible shared context. |
| `memory_set_pin` | Pin or unpin an entry using `pinned`. |
| `memory_link` | Link memory to a task. |
| `journal_write` / `journal_list` | Write or read the caller's durable journal. |
| `agent_journal_list` | Read an eligible subordinate journal. |

### Planning and decisions

| Tool | Purpose |
| --- | --- |
| `area_list` / `area_get` | Read Planning Areas. |
| `area_create` / `area_update` / `area_archive` | Manage writable Areas. |
| `area_link` | Add or remove task, decision, initiative, or Area links. |
| `area_note` | Create, update, or archive an Area note. |
| `initiative_list` / `initiative_get` | Read Initiatives. |
| `initiative_create` / `initiative_update` / `initiative_archive` | Manage Initiatives. |
| `initiative_link` | Add or remove task or decision links. |
| `decision_list` / `decision_get` | Read visible decisions. |
| `decision_create` / `decision_update` | Create or update decisions or proposals. |
| `decision_review` | Accept, reject, or revise a proposed decision. |
| `decision_link` | Link a visible decision to related context. |

`area_link` and `initiative_link` replace separate link/unlink operations.
Pass `operation: add|remove`, `target_kind`, and `target`.

Decision acceptance is distinct from decision editing. Status transitions that
make a decision authoritative require `decision.accept`.

### Thinking and Idea Briefs

| Tool | Purpose |
| --- | --- |
| `idea_brief_list` / `idea_brief_get` | Read Idea Briefs. |
| `idea_brief_create` / `idea_brief_update` | Create or revise an Idea Brief. |
| `idea_brief_transition` | Propose, park, or archive an Idea Brief. |
| `thinking_list` / `thinking_get` | Read Scratchpads or Mind Maps by `artifact_type`. |
| `scratchpad_update` | Create or update a Scratchpad. |
| `mind_map_update` | Create or update a Mind Map. |
| `mind_map_node_update` | Create, update, move, or delete a node. |
| `mind_map_link_update` | Create, update, or delete a link. |
| `thinking_archive` | Archive a Scratchpad or Mind Map. |

Create/update/delete variants are selected through `operation`, rather than
separate tool names.

### Worktrees

| Tool | Purpose |
| --- | --- |
| `worktree_diff` | Read a bounded structured diff. |
| `worktree_merge` | Run the configured PR or direct merge flow. |
| `worktree_rebase` | Rebase a conflicted controlled branch. |
| `worktree_create_pr` | Push and open a create-only pull request. |
| `worktree_checkpoint` | Snapshot work before a risky operation. |
| `worktree_remove` | Remove an eligible worktree. |
| `worktree_adopt` | Adopt an orphaned worktree. |
| `worktree_advance_boundary` | Advance a shared worktree boundary. |

### Architect administration

| Tool | Purpose |
| --- | --- |
| `engineer_hire` | Request a user-approved Engineer hire. |
| `engineer_update` | Update an Engineer's specialization metadata. |
| `engineer_lifecycle` | Dismiss, rehire, or restore an Engineer using `operation`. |
| `hire_list` | List pending hires or read one hire. |
| `feedback_request` / `feedback_status` | Request and inspect Engineer feedback. |
| `group_health_brief` | Read group anomalies and responsible actors. |
| `attention_digest` / `wave_summary` / `completion_audit` | Read orchestration rollups. |
| `deploy_get` | Read deployment state. |
| `settings_get` | Read visible process/Architect settings. |
| `telemetry_query` | Query visible MCP call telemetry. |

### Behavior overlays and event delivery

| Tool | Purpose |
| --- | --- |
| `event_list` | Read recent visible events. |
| `event_delivery_update` | Configure delivery or resume it using `operation`. |
| `behavior_overlay_get` | Read current visible overlay state. |
| `behavior_overlay_versions` / `behavior_overlay_diff` | Inspect overlay history. |
| `behavior_overlay_proposal_list` | List visible proposals. |
| `behavior_overlay_propose` | Propose for self, agent, or role using `target_kind`. |
| `behavior_overlay_review` | Approve or reject using `decision`. |
| `behavior_overlay_rollback` | Request or perform an eligible rollback. |

## Capability relationships

Communication capabilities are relationship-based:

- `message.peer`
- `message.subordinate`
- `message.supervisor`
- `message.user`
- `message.ack_request`

The previous recipient-kind capabilities are migrated when old Agent Class
YAML or frozen authority snapshots are loaded. New definitions should use only
the relationship vocabulary.

`task.board_sync.read` controls whether `board_sync` fields appear in task
results. It is independent from ordinary task visibility.

## Compatibility

Historical role-prefixed names remain callable during migration when the same
caller would have been authorized. They are not returned by `tools/list`, are
not returned by `tool_search`, and should not appear in prompts or new code.

## See also

- [Agent Classes](agent-classes.md)
- [Architecture](architecture.md)
- [Pipelines](../tasks/pipelines.md)
