# Agent Classes generic authority and prompt plan

> Superseded by
> [`agent-class-capability-acl-rfc.md`](agent-class-capability-acl-rfc.md),
> which records the accepted implementation design. This file is retained as
> historical design context only.

Status: superseded historical design notes.


Current implementation notes:

- Built-in class behavior is now YAML data plus generic ACL/prompt rendering.
- Status contracts named after built-in classes have been removed from code, UI fixtures, and non-historical docs.
- Normal Agent Class authoring uses `acl:` with one mode at a time: `mode: allow` + `allow`, or `mode: deny` + `deny`. Legacy `policy.mode`/capability-bucket input is accepted only for migration/backcompat.
- ACL scopes use `self`, `children`, `group`, and `global`; hiring/owned-worker relations compile to `children`.

Purpose: track the action items for replacing class-specific Agent Class code
with a generic, user-customizable model. Agent Classes must be fully
customizable by users; Torque code must not encode behavior for particular
classes such as Product Manager, Creative, or Torque Steward.

## Problem statement

The current Agent Class implementation mixes two incompatible models:

1. Agent Classes are presented as user-customizable YAML-defined templates.
2. Runtime code still recognizes specific class ids/archetypes and emits
   class-specific previews, validation, prompt guidance, and MCP projection
   exceptions.

That second model needs to go. Built-in classes can remain as bundled YAML
examples/defaults, but they must use the same generic machinery available to
custom classes.

## Guiding decisions

- Agent Class YAML is the source of class identity, prompt text, and selected
  authority.
- Torque code should understand base kinds, tools, capabilities, denials,
  lifecycle, and generic launch snapshots.
- Torque code should not understand named class semantics like
  `is_creative`, `product_manager_status`, or `torque_steward_status`.
- Prompt assembly should be generic and data-driven.
- Enforcement should focus on capabilities/tool access. Policy prose that does
  not enforce tool access should be removed.

## Action item #1 — Replace class-specific prompt assembly with generic prompts

### Current state

Architect prompt assembly currently derives booleans such as:

- `is_product_manager`
- `is_creative`
- `is_torque_steward`

Those flags shape identity text, available-tool guidance, boot checklists,
operating guidelines, user-message guidance, and autonomy-policy language.
This means a custom class with the same capabilities as a built-in class will
not receive equivalent prompt behavior unless the code is changed for that
class too.

### Target state

Prompt assembly becomes a generic renderer over the frozen launch snapshot:

1. Resolve the effective Agent Class snapshot and effective capability/tool
   surface.
2. Render base-kind framing only when no restricted class prompt is active.
3. For restricted/custom classes, render:
   - class identity from YAML (`display_name`, `identity`, base kind);
   - generic authority boundary from visible MCP tools/capabilities;
   - generic available/unavailable capability summary;
   - class-provided prompt sections;
   - generic warnings;
   - generic autonomy/checkpoint policy bounded by the same authority.
4. Append behavior overlays/custom instructions only when the effective
   capability surface permits them.

No prompt branch should check for a specific class id, class archetype, or
generated internal profile id.

### Proposed YAML shape

Agent Class prompts use structured sections only:

```yaml
prompt:
  identity: >
    You are an imaginative but grounded ideation partner for Torque.
  job: >
    Shape product understanding and produce safe proposal artifacts within
    projected authority.
  boot_checklist:
    - Confirm class, group, and visible tools.
    - Read projected context before proposing changes.
  operating_guidelines:
    - Separate observations, inferences, options, risks, and non-goals.
    - Treat proposals as non-binding until accepted by an authorized actor.
  tool_guidance:
    - when_capability: thinking_workspace
      text: Use visible Thinking tools for caller-owned Scratchpad notes and Mind Maps.
```

The important property is that class behavior
comes from data, not Python `if class_id == ...` branches.

### Implementation notes

- Replace `_architect_prompt_authority_context()` class identity flags with
  generic facts:
  - class id/version/label/base kind;
  - lifecycle/status;
  - final granted capability atoms;
  - final denied capability atoms;
  - visible tool names/tool families;
  - projected high-risk categories.
- Replace `_restricted_identity_sentence()`,
  `_restricted_tool_lines()`, `_restricted_boot_lines()`, and
  `_restricted_operating_lines()` with capability-driven and YAML-driven
  renderers.
- The built-in Creative/Product Manager/Steward prompts should be moved fully
  into their YAML prompt sections.
- Add a regression test with a custom class that has Creative-like prompt
  sections/capabilities but a completely different id. It should receive the
  same generic prompt structure without any code changes.

### Acceptance criteria

- No production prompt assembly code branches on a specific Agent Class id,
  generated internal profile id, or archetype.
- Built-in classes continue to launch with useful prompts, but their
  class-specific text lives in YAML.
- Custom classes can define equivalent prompt behavior using only YAML.
- Default full Architect/Engineer/Worker classes preserve current base-kind
  behavior unless explicitly assigned custom prompt sections.

## Action item #2 — Drop status contracts and `policy:` as authority sources

### Drop status contracts

The current preview/snapshot layer adds class-specific status contracts such as:

- `product_manager_status`
- `creative_architect_status`
- `torque_steward_status`

These were useful while writing the built-in classes, but they are not a
general product model. They make the UI/API depend on named built-ins and
cannot scale to user-created classes.

Remove these contracts entirely.

Replace them, if needed, with generic data derived from the final capability
surface:

```yaml
authority_summary:
  lifecycle: stable
  status: restricted
  base_kind: architect
  granted_capability_count: 12
  denied_capability_count: 8
  visible_tool_count: 20
  high_risk_grants: []
  unavailable_high_risk_categories:
    - worker_dispatch
    - deploy_admin
```

Optional human-facing class facts should be declared in YAML, not hardcoded:

```yaml
operator_summary:
  bullets:
    - Proposal-only ideation partner.
    - Uses Thinking and Idea Brief tools when granted.
```

### Drop `policy:` as an Agent Class authority source

The current `policy:` block mostly records intent:

```yaml
policy:
  mode: compile
  scope: ...
  communication: ...
  spawn: ...
  audit: ...
```

Only `policy.mode` materially affects compilation. The nested scope,
communication, spawn, and audit fields do not directly enforce MCP access.
That is confusing because a user may reasonably expect
`policy.spawn.dispatch: deny` to block dispatch.

We should remove `policy:` from the normal Agent Class authoring model.

Authority should be represented by the MCP ACL only:

```yaml
acl:
  mode: allow
  allow:
    - action: planning.area.read
      scope: group
    - action: message.user
      scope: self
```

During migration we can continue accepting old YAML with `policy.mode:
compile`, but new docs/UI should present an ACL-only model. Legacy
capability buckets can remain as ACL aliases while we migrate existing YAML and
UI controls.

### Implementation notes

- Stop adding class-specific status keys to previews and frozen snapshots.
- Update frontend/tests/docs that expect those keys.
- Keep backward-compatible loading for existing built-in YAML while migrating
  the files to the new shape.
- Remove `policy.scope`, `policy.communication`, `policy.spawn`, and
  `policy.audit` from generated internal profile snapshots unless retained as
  opaque legacy metadata for migration only.
- Make docs and UI clear that the ACL is the authority model for Torque MCP
  tools.

### Acceptance criteria

- Agent Class previews/snapshots contain no `*_status` contract keyed to a
  named class.
- Agent Class authoring docs no longer describe `policy:` as an authority
  mechanism.
- A user can understand class authority from ACL mode, allowed or denied actions,
  scopes, tool families, exact tools, and visible MCP tools.
- Existing class-specific status-contract tests are removed or rewritten around
  generic authority summaries.

## Action item #3 — Replace capabilities/policies with a generic MCP ACL

### Product direction

Agent Class authority should be understandable as an ACL over MCP tooling.

There should be two simple modes:

1. **Allow mode** — the class can perform only explicitly allowed actions.
2. **Deny mode** — the class can perform everything in its base-kind ceiling
   except explicitly denied actions.

Both modes control the Torque MCP tool surface. Prompt guidance, previews, and
launch snapshots should be generated from the same ACL so the agent sees the
same authority the server enforces.

### Current capability surface by base kind

These are the current internal capability atoms available to each base kind.
They are useful as migration inventory, but the new authoring model should not
force users to think in these internal atom names unless they open an advanced
view.

#### Worker ceiling

| Area | Current atoms |
|---|---|
| Observe | `observe.self_context`, `observe.task_detail` |
| Planning | `planning.area_read` |
| Task | `task.complete`, `task.verify`, `task.upload_artifact` |
| Communication | `comm.user_ask`, `comm.user_message` |
| Journal | `journal.private` |
| Memory | `memory.read`, `memory.publish` |

#### Engineer ceiling

Engineer includes all Worker atoms plus:

| Area | Current atoms |
|---|---|
| Observe | `observe.board_summary`, `observe.events`, `observe.mcp_calls`, `observe.semantic_recall` |
| Planning | `planning.initiative_read` |
| Task | `task.create`, `task.update`, `task.reassign`, `task.move`, `task.dispatch`, `task.mark_covered`, `task.board_sync_read` |
| Decisions | `decision.list`, `decision.create`, `decision.update`, `decision.link` |
| Agent control | `agent.dispatch_worker` |
| Communication | `comm.engineer_message`, `comm.worker_message` |
| Journal | `journal.read`, `journal.write` |
| Memory | `memory.admin` |
| Worktree | `worktree.read`, `worktree.merge` |

#### Architect ceiling

Architect includes the broadest Torque authority:

| Area | Current atoms |
|---|---|
| Observe | `observe.self_context`, `observe.board_summary`, `observe.task_detail`, `observe.events`, `observe.mcp_calls`, `observe.deploy_state`, `observe.semantic_recall` |
| Planning | `planning.area_read`, `planning.area_write`, `planning.initiative_read`, `planning.initiative_write` |
| Task | `task.create`, `task.create_queued`, `task.update`, `task.update_planning_fields`, `task.reassign`, `task.move`, `task.move_planning_safe`, `task.dispatch`, `task.mark_covered`, `task.verify`, `task.complete`, `task.upload_artifact`, `task.board_sync_read` |
| Decisions | `decision.list`, `decision.create`, `decision.create_proposed`, `decision.update`, `decision.update_proposed`, `decision.accept`, `decision.link` |
| Agent control | `agent.engineer_roster_read`, `agent.hire_engineer`, `agent.manage_engineer_roster`, `agent.dispatch_worker` |
| Communication | `comm.user_ask`, `comm.user_message`, `comm.engineer_message`, `comm.worker_message`, `comm.peer_architect_list`, `comm.peer_architect_message`, `comm.product_ack_request` |
| Journal | `journal.private`, `journal.read`, `journal.write` |
| Memory | `memory.read`, `memory.publish`, `memory.admin` |
| Worktree | `worktree.read`, `worktree.merge` |
| Deploy/admin | `deploy.apply`, `admin.settings` |
| Profile/class admin | `profile.assign`, `profile.edit` |
| Thinking | `thinking.read`, `thinking.write_own` |
| Idea Briefs | `idea_brief.read`, `idea_brief.write_own`, `idea_brief.propose` |
| Behavior overlays | `behavior_overlay.read`, `behavior_overlay.propose_self` |

### Why capability atoms alone are insufficient

Atoms answer "what kind of action can be attempted?" They do not fully answer
"on which resource?"

Examples:

- `comm.engineer_message` could mean:
  - message only Engineers hired by this Architect;
  - message any Engineer in the same group;
  - message Engineers in any group.
- `task.reassign` could mean:
  - reassign only tasks created by this agent;
  - reassign tasks in this group;
  - reassign tasks across groups.
- `journal.read` could mean:
  - read only this agent's journal;
  - read owned Workers' journals;
  - read peer Engineer/Architect journals;
  - read all group journals.

Today these resource rules are scattered through tool handlers. The ACL model
should make them explicit enough for previews, prompts, tests, and enforcement.

### Proposed ACL structure

Agent Classes should store an ACL, not `policy:` prose and not named built-in
status contracts.

```yaml
acl:
  mode: allow # allow | deny
  allow:
    - action: message.engineer
      scope: children
    - action: task.read
      scope: group
    - tool: architect_task_propose
```

In deny mode:

```yaml
acl:
  mode: deny
  deny:
    - action: deploy.apply
    - action: worktree.merge
    - action: class.admin
```

Interpretation:

- `mode: allow` starts from no Torque MCP tools and adds only matching tools.
- `mode: deny` starts from the base-kind ceiling and removes matching tools.
- `allow` and `deny` entries can refer to:
  - `tool`: one exact MCP tool name;
  - `family`: a glob over MCP tool names, such as `architect_proposal_*`;
  - `action`: a stable semantic action key;
  - `capability`: a legacy/internal capability atom or bucket, for migration.
- The modes are exclusive: `mode: allow` must not include `deny`, and `mode: deny` must not include `allow`. Validation rejects mixed ACLs.
- In allow mode, anything not matched by `allow` is denied by default.
- Cross-base-kind escalation is impossible: an Architect class can only select
  Architect-ceiling tools, an Engineer class only Engineer-ceiling tools, etc.
- External connector tools are out of scope until connector governance is
  designed; the ACL governs Torque MCP tools first.

### Action keys

The ACL should introduce stable semantic action keys between user-facing class
config and raw MCP tool names.

Example:

| Action key | Example tools | Notes |
|---|---|---|
| `self.read` | `torque_context`, boot/context tools | Own identity/session/task context. |
| `help.read` | `*_help_list`, `*_help_show`, `*_help_search`, `*_help_query` | Maintained docs only. |
| `task.read` | task list/show/board summary tools | Scope decides self, children, group, or global. |
| `task.report` | worker/engineer progress, done, verify, upload artifact | Usually self/assigned-task scoped. |
| `task.create` | raw task create tools | Executable task creation. |
| `task.propose` | product task proposal tools | Queued/non-dispatched proposals. |
| `task.update` | task edit/update tools | Scope should be explicit. |
| `task.move` | task lane movement tools | High-risk unless planning-safe. |
| `task.dispatch` | dispatch/batch dispatch tools | High-risk/critical. |
| `planning.area.read` | Area list/show tools | Usually `group` scoped. |
| `planning.area.write` | Area create/update/archive/link/note tools | Scope decides self/children/group/global. |
| `planning.initiative.read` | Initiative list/show tools | Usually `group` scoped. |
| `planning.initiative.write` | Initiative create/update/archive/link tools | Scope decides self/children/group/global. |
| `decision.read` | decision list/show-ish tools | Scope decides self/children/group/global plus existing visibility rules. |
| `decision.propose` | proposed decision wrappers | Cannot accept decisions. |
| `decision.write` | raw decision create/update/link | High-risk unless restricted by status/scope. |
| `decision.accept` | accepted/revised/rejected decision authority | Separate from generic write. |
| `engineer.roster.read` | engineer list/status tools | Scope decides children/group/global. |
| `engineer.manage` | hire/dismiss/rehire/restore/specialization tools | Critical. |
| `worker.dispatch` | worker dispatch tools | Critical. |
| `message.user` | user ask/message/reply tools | Scope is usually self's user thread/context. |
| `message.engineer` | architect→engineer or engineer↔engineer tools | Scope required. |
| `message.worker` | engineer/architect→worker tools | Scope required. |
| `message.architect_peer` | architect peer list/message/inbox/reply | Scope required. |
| `journal.private` | private journal read/write | Own journal only. |
| `journal.scoped` | read/write scoped journals | Scope required. |
| `memory.read` | memory list/read | Scope required if memory visibility expands. |
| `memory.write` | memory publish | Scope required. |
| `memory.admin` | pin/link/unpin | High-risk. |
| `worktree.read` | diff/worktree status | Scope required. |
| `worktree.merge` | merge/rebase/PR/checkpoint/remove/adopt | Critical; split further if needed. |
| `deploy.read` | deploy state | Sensitive read. |
| `deploy.apply` | deploy/restart/admin runtime changes | Critical. |
| `settings.admin` | global/team/runtime settings | Critical. |
| `class.admin` | Agent Class assign/edit tools | Critical. |
| `behavior_overlay.read` | overlay read/diff/version/proposal list | Scope required. |
| `behavior_overlay.propose` | propose own overlay | Usually self-only. |
| `behavior_overlay.admin` | approve/reject/rollback/propose for others | Critical. |
| `thinking.read` | Thinking artifact reads | Scope decides self/children/group/global plus linked-context filters if needed. |
| `thinking.write` | caller-owned Scratchpad/Mind Map writes | Own-created by default. |
| `idea_brief.read` | Idea Brief reads | Scope decides self/children/group/global plus linked-context filters if needed. |
| `idea_brief.write` | Idea Brief create/update/refine/park/archive/propose | Own-created by default. |
| `tool.search` | `*_tool_search` | Should be selectable/deniable like any other tool family. |
| `telemetry.read` | event/MCP-call/digest tools | Sensitive read; scope required. |

The implementation should generate MCP tool access by mapping each MCP tool to
one action key plus optional operation metadata. Capability atoms can be
retained internally during migration, but the ACL authoring layer should prefer
action keys/tool families.

### Scope predicates

Keep scopes intentionally small. The ACL should not expose every internal
ownership relation as a separate user-facing word. Use this vocabulary:

| Scope | Meaning | Examples |
|---|---|---|
| `self` | Only the caller agent/cell/session and resources inherently owned by that caller. | own context, own private journal, own Scratchpad/Idea Brief updates, assigned task reporting |
| `children` | Direct subordinate/child resources for the caller's base kind. | Architect → Engineers it hired; Engineer → Workers it owns; parent task → derived child tasks when applicable |
| `group` | Any matching resource/agent in the caller's Torque group. | same-group tasks, Areas, Initiatives, Architects, Engineers, Workers, Thinking artifacts |
| `global` | Cross-group/global authority. | cross-group messaging, global settings/admin, cross-group operational reads |

Default rule: if a resource action has no scope, it is invalid in class YAML
unless the action is explicitly self-only by definition.

`global` must never be implied by deny mode. It must be explicit, high-risk,
and usually require a UI confirmation or a separate product design.

The ACL scope is a maximum visibility/authority ceiling, not a replacement for
tool-specific resource checks. If a tool is currently narrower than the ACL
word, the handler remains authoritative. Example: an Engineer tool that only
messages its hiring Architect can stay parent-only internally even if the ACL
entry uses `group`, until broader group Architect messaging is deliberately
implemented.

### Answering the Engineer messaging example

The ACL should distinguish the action from the resource scope.

```yaml
acl:
  mode: allow
  allow:
    - action: message.engineer
      scope: children
```

This means an Architect can message only Engineers it hired.

Other possible scopes:

```yaml
- action: message.engineer
  scope: group
```

Allows messaging any Engineer in the same group, regardless of hiring
Architect.

```yaml
- action: message.engineer
  scope: global
```

Allows cross-group Engineer messaging. This should be critical-risk, require
explicit UI confirmation in the class editor, and probably remain unavailable
until cross-group messaging is deliberately designed.

For Engineers, the same vocabulary applies:

```yaml
- action: message.worker
  scope: children
```

Allows an Engineer to message only Workers it owns.

```yaml
- action: message.engineer
  scope: group
```

Allows Engineer peer messaging within the same Torque group, subject to the
current tool handler's narrower relationship checks until broader group-peer
semantics are implemented.

### Deny mode with scopes

Deny mode should still support scoped denials:

```yaml
acl:
  mode: deny
  deny:
    - action: message.engineer
      scope: global
    - action: deploy.apply
    - action: worktree.merge
```

Interpretation:

- Start from the base-kind default tool surface.
- Remove all deploy and merge tools.
- Remove only cross-group Engineer messaging if such a tool/scope is ever
  otherwise present.

If an unscoped denial is provided, it removes the action for all scopes:

```yaml
- action: message.engineer
```

### Enforcement model

The ACL should have two enforcement layers:

1. **Tool projection** — decide whether an MCP tool is visible/callable at all.
2. **Resource authorization** — decide whether this specific call is allowed
   for this caller and target resource.

Tool projection can be computed at session launch and used for visible MCP tool
lists. Resource authorization must run inside tool handlers because the target
resource is known only when arguments are supplied.

For example:

```yaml
- action: message.engineer
  scope: children
```

Tool projection can show `architect_engineer_message`, but the handler must
still reject a target Engineer not hired by the caller.

This keeps the UI simple while preserving hard runtime checks.

### ACL evaluation algorithm

1. Determine the caller base kind.
2. Load that base kind's maximum tool/action ceiling.
3. Expand the Agent Class ACL entries into normalized allowed/denied entries:
   - exact tools;
   - tool families;
   - action keys;
   - legacy capability/bucket aliases.
4. If `mode: allow`, start with an empty set.
5. If `mode: deny`, start with the base-kind ceiling.
6. In allow mode, apply only `allow` entries. In deny mode, apply only `deny` entries.
7. Reject any selected action/tool outside the base-kind ceiling.
8. Require explicit scopes for resource-touching actions.
9. Generate:
    - visible MCP tool list;
    - frozen ACL snapshot;
    - prompt authority summary;
    - UI preview.

### Relationship to existing MCP tool wrappers

Tool wrappers such as `architect_proposal_*` should be treated as normal MCP
tools/tool families, not as Product Manager-specific magic.

For example:

```yaml
acl:
  mode: allow
  allow:
    - family: architect_proposal_*
    - family: architect_thinking_*
```

This is easy to understand and does not require the code to know the class is
"Product Manager" or "Creative".

If we keep action keys, those wrappers can also map to action keys such as
`task.propose`, `decision.propose`, `idea_brief.write`, and
`thinking.write`.

### Migration from current capabilities

Migration should preserve existing behavior while changing the authoring model:

1. Keep current capability atoms as internal compatibility aliases.
2. Build a complete MCP tool → action/scope/legacy-capability mapping using the simple scope vocabulary.
3. Translate existing class `capabilities.buckets` into ACL `allow` entries.
4. Translate existing `capabilities.restrictions` into ACL `deny` entries.
5. Remove `policy:` from built-in class YAML after equivalent ACL entries
   exist.
6. Remove hardcoded raw deny lists once the MCP mapping is complete.
7. Add doctor checks for:
   - unmapped MCP tools;
   - ACL entries outside base-kind ceiling;
   - resource actions without scopes;
   - deny-mode classes that implicitly expose newly added critical tools.

### Resolved ACL simplifications

Use the smallest user-facing vocabulary that covers current Architect, Engineer,
and Worker relationships:

- two modes only: `allow` and `deny`;
- resource scopes only: `self`, `children`, `group`, and `global`;
- no relationship-specific scope words for hiring or worker ownership; those
  relationships compile to `children` for the relevant base kind;
- denials always win over allows;
- exact tools and tool families are first-class ACL entries;
- legacy capability buckets remain only as migration aliases until the MCP
  tool/action mapping is complete.

Remaining separate design work: external connectors. This ACL currently governs
Torque MCP tools, not Codex/GitHub/Gmail/Slack/Calendar connector tools.
