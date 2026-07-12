# RFC: Generic Agent Class capability ACLs and prompt assembly

Status: Accepted for implementation  

## Implementation checkpoint (2026-07-11)

Implemented in the current working change:

- canonical capability catalog and scoped allow/deny evaluator;
- schema-v5 built-in and custom Agent Class authoring;
- frozen effective-authority snapshots with registry hashes;
- fail-closed authority coverage for Worker, Engineer, and Architect MCP tools;
- eager, deferred, tool-search, and call-time projection from frozen authority;
- initial generic concrete-target scope enforcement for agents, tasks,
  messaging, telemetry, behavior overlays, and task mutations;
- capability/scope-based editor and generic Agent Class prompt authority
  summaries;
- generic Architect-derived and Engineer-derived base prompt composition for
  class-launched sessions;
- removal of generated `class-policy-*` Agent Profiles from Agent Class
  preview, validation, launch, status, audit, and UI paths;
- removal of the legacy bucket/profile compiler and generated-profile
  compatibility fields from the Agent Class implementation;
- canonical authority descriptors colocated on all 227 registered MCP tool
  specs, including explicit projection minima and concrete target metadata;
- removal of the three legacy surface capability maps and the centralized
  capability-to-target-argument heuristic;
- descriptor-driven call-time checks for scalar and list-valued Agent/Task
  targets, plus fail-closed result filtering for self-scoped task lists;
- descriptor-driven scope resolution for Thinking notes, Mind Maps, Idea
  Briefs, MCP telemetry rows, and reply-message counterparties, including
  self-scoped collection filtering and cross-scope target concealment;
- descriptor-enforced scope-valued arguments, multi-target task operations,
  child-roster filtering, and group-only minima for role-wide behavior-overlay
  administration;
- fail-closed registration validation requiring every scoped requirement to
  declare target, result, scope-argument, or explicit handler enforcement;
- product-peer, acknowledgement, and behavior-overlay wrapper refinements now
  consult frozen canonical Agent Class authority;
- removal of direct Agent Profile assignment, policy evaluation, persistence,
  audit, doctor, server, WebSocket, UI, built-in definition, and test surfaces;
- removal of Agent Profile fields from `AgentCell`, fresh database schemas, and
  Agent Class audit records;
- rename of the remaining authority capability from `class_profile.admin` to
  `class.admin`;
- rename of the class-named Steward brief module/tool to the generic
  `group_health_brief` / `architect_group_health_brief` surface;
- replacement of Product-Manager-specific task labels, decision metadata,
  route scopes, and proposal-root cleanup names with product-proposal
  semantics;
- replacement of the `architect_product_*` wrapper namespace with semantic
  proposal, task-proposal, decision-proposal, Idea Brief, and peer-tool names.
- descriptor-driven `self`/`group` filtering and concrete-target checks for
  Planning Areas and Initiatives across Worker, Engineer, Architect, and
  proposal read surfaces;
- concrete ownership checks for Area and Initiative mutations and proposed
  decision updates, with create operations retaining explicit handler
  enforcement because no target exists before creation;
- alignment of Initiative write authority with the handler's actual
  caller-owned (`self`) platform ceiling instead of advertising unsupported
  group-wide mutation authority.
- split of the mixed-purpose `architect_reply` surface into
  `architect_engineer_reply` and `architect_peer_reply`, so each projected
  tool maps to exactly one message capability;
- strict Architect-only row filtering for `architect_peer_inbox`, removing
  its former dependency on `message.engineer` and preventing Engineer threads
  from leaking through a peer-Architect read surface.
- descriptor-driven `self`/`children`/`group` filtering for recent event rows,
  using task and attributed-agent relationships rather than treating the
  entire event feed as an indivisible group-wide aggregate.

Still required before this RFC is complete:

- finish family-by-family list filtering and concrete resource scope checks
  for semantic recall rows, linked Planning context embedded in aggregates,
  and remaining aggregate/read models;
Date: 2026-07-11  
Supersedes: `docs/plans/agent-classes-generic-authority-plan.md`

## Summary

Agent Classes must be completely user-customizable. Torque runtime code must
not recognize Product Manager, Creative, Torque Steward, or any other named
class. Built-in classes are ordinary YAML definitions using the same schema,
capability catalog, prompt renderer, validation, projection, and runtime
authorization as user-created classes.

An Agent Class has two independent responsibilities:

1. provide structured prompt content; and
2. select a capability ACL that controls Torque MCP tools.

The ACL is the sole Agent Class authority source. It has one mode (`allow` or
`deny`) and one rule list. Capabilities are stable semantic operations such as
`task.read`, `message.engineer`, and `worktree.merge`. There is no separate
public action layer, capability-bucket layer, restriction-bucket layer, policy
block, status contract, or generated Agent Profile policy.

Every Torque MCP tool declares its capability requirements beside its tool
definition. Those declarations drive tool projection, call-time checks,
resource-scope authorization, UI previews, prompt authority summaries,
documentation, and coverage audits.

## Motivation

The current implementation is transitional rather than generic:

- Agent Classes compile into generated `class-policy-*` Agent Profiles.
- Authority is represented through overlapping capability atoms, capability
  buckets, restriction buckets, action aliases, exact tools, and tool-family
  globs.
- Exact-tool and family matches are accepted before capability requirements,
  so they can bypass the semantic capability model.
- ACL scopes survive only in preview metadata; generated profile projection
  does not enforce them.
- Resource authorization remains distributed through handler-specific legacy
  ownership checks with no common effective-scope contract.
- Restricted Architect prompt assembly contains hardcoded capability-specific
  behavior instead of treating the class prompt as the behavioral source of
  truth.
- MCP tool-to-capability mappings live in a central dictionary that can drift
  from actual tool registration.

At the time this RFC was accepted, Torque registered 227 MCP tools. Of those,
226 had current central capability mappings, one registered tool was unmapped
(`torque_stop_user_message_loop`), and one mapping referred to a stale tool
(`architect_product_idea_brief_promote`, now removed). This is sufficient evidence that
authority metadata must be colocated with tool registration and audited
fail-closed.

## Goals

- Make every built-in and custom Agent Class use identical generic machinery.
- Make the class ACL the only Agent Class authority source.
- Give users a simple allow-list or deny-list model.
- Use one capability vocabulary from YAML through runtime enforcement.
- Support `self`, `children`, `group`, and `global` resource scopes where the
  underlying capability implements them.
- Enforce the ACL both when projecting tools and when authorizing a concrete
  target resource.
- Freeze effective authority at session launch so on-disk changes do not alter
  a running session.
- Generate prompt authority summaries, previews, docs, and diagnostics from
  the same effective authority snapshot used by MCP enforcement.
- Fail validation when a Torque MCP tool lacks authority metadata.

## Non-goals

- Agent Class ACLs do not govern external connector tools in this RFC.
- The ACL does not control lifecycle, identity, prompt prose, custom
  instructions, behavior overlays, checkpoint cadence, or autonomy settings.
- The ACL cannot grant authority beyond the platform ceiling implemented for
  the selected base kind.
- Scope words do not create new cross-group behavior. A scope is selectable
  only after the corresponding handlers implement and test it.
- This RFC does not remove the base runtime kinds (`architect`, `engineer`, and
  `worker`). Base kinds remain platform-level ceilings and runtime contracts.

## Terminology

### Capability

A stable semantic permission representing one operation, for example:

- `task.read`
- `task.create`
- `task.dispatch`
- `decision.propose`
- `decision.accept`
- `message.engineer`
- `thinking.write`
- `worktree.merge`

Capabilities are the ACL actions. There is no second public `action` namespace.

### Tool requirement

Authority metadata attached to an MCP tool. A tool may require one or more
capabilities. The tool is projectable only when every unconditional
requirement is satisfied.

### Platform ceiling

The maximum capability and scope supported for a base kind. An Agent Class can
only narrow that ceiling; it cannot create a new base-kind power.

### Effective authority

The normalized capability-to-scope map produced by evaluating an Agent Class
ACL against its base-kind ceiling. It is frozen at session launch.

## Agent Class schema

The canonical ACL shape is:

```yaml
agent_class_schema_version: 5
id: planning-partner
version: "1"
base_kind: architect
display_name: Planning Partner
description: A planning-focused Architect-derived class.
lifecycle: stable

prompt:
  identity: >
    You are a careful planning partner for Torque.
  job: >
    Clarify product scope and produce reviewable planning artifacts.
  boot_checklist:
    - Confirm class, group, and visible tools.
    - Read projected context before proposing changes.
  operating_guidelines:
    - Separate observations, inferences, options, risks, and non-goals.
    - Treat proposals as non-binding until accepted by an authorized actor.
  tool_guidance:
    - when_capability: thinking.write
      text: Use visible Thinking tools for caller-owned exploration artifacts.

acl:
  mode: allow
  rules:
    - capability: self.read
      scope: self
    - capability: task.read
      scope: group
    - capability: message.architect_peer
      scope: group
```

The deny form uses the same rule key:

```yaml
acl:
  mode: deny
  rules:
    - capability: deploy.apply
    - capability: worktree.merge
    - capability: message.engineer
      scope: group
```

### Schema invariants

- `acl.mode` is required and is exactly `allow` or `deny`.
- `acl.rules` is the only ACL rule collection.
- Each rule names exactly one known capability.
- Scoped capabilities require `scope`.
- Unscoped capabilities reject `scope`.
- The selected scope must be supported by the capability and base kind.
- Duplicate rules for the same capability are rejected rather than merged
  implicitly.
- Unknown capabilities are errors.
- Raw capability atoms, capability buckets, restriction buckets, exact-tool
  grants, and tool-family globs are not part of schema v5.

Exact-tool and family ACL entries are intentionally omitted. They create two
competing authority models, and family globs can silently grant future tools.
If two tools sharing a capability need to be controlled independently, the
capability is too broad and must be split.

## ACL evaluation

Scopes form an ordered authority boundary:

```text
self < children < group < global
```

Not every capability supports every scope. The capability registry declares
its valid scopes and its maximum scope for each base kind.

### Allow mode

1. Start with no capabilities.
2. For each rule, validate the capability against the base-kind ceiling.
3. Grant the requested scope and all narrower applicable scopes.
4. Reject a requested scope above the platform ceiling.

Example: `message.engineer: children` grants messaging of direct child
Engineers only.

### Deny mode

1. Start with the complete base-kind ceiling.
2. An unscoped rule removes an unscoped capability completely.
3. An unscoped rule for a scoped capability removes the capability completely.
4. A scoped rule removes that scope and every broader scope, leaving the next
   narrower supported scope as the effective maximum.

Example: denying `message.engineer` at `group` removes same-group and global
messaging while leaving at most `children` authority.

This boundary interpretation makes scoped deny rules useful for narrowing a
mostly-full class without introducing simultaneous allow and deny lists.

### Future capabilities in deny mode

Deny mode intentionally follows the base-kind ceiling. A new capability added
to that ceiling is available to a deny-mode class unless denied. This is the
meaning of "everything except these rules." Existing running sessions remain
unchanged because effective authority is frozen at launch. Class preview must
show authority changes before a relaunch.

## Scope semantics

The user-facing vocabulary is deliberately limited to four words, but each
capability declares which words are meaningful.

| Scope | Generic meaning |
|---|---|
| `self` | The caller and resources directly owned by or inherently assigned to the caller. |
| `children` | Direct subordinate agents and capability-defined resources belonging to those direct subordinates. |
| `group` | Matching resources and agents in the caller's Torque group. |
| `global` | Cross-group or process-global authority. |

`children` is interpreted by resource type. For agent-targeting capabilities,
it means direct reports: Architect to hired Engineers and Engineer to owned
Workers. For other resource types, the capability specification must define a
deterministic relationship before `children` can be offered.

The UI and validator expose only implemented scopes. For example,
`journal.private` is `self` only, while `settings.admin` is a global/unscoped
operation. A handler that currently supports only child Engineer messaging
must not advertise `group` merely because the generic vocabulary contains it.

## Capability registry

Torque owns a generic capability registry. A capability definition contains:

```python
CapabilityDefinition(
    id="message.engineer",
    label="Message Engineers",
    description="Send and reply to Engineer messages.",
    risk="high",
    scopes=("children", "group", "global"),
    ceilings={
        "architect": "children",
        "engineer": "group",
    },
)
```

The exact ceilings must reflect implemented runtime behavior, not aspirational
behavior. Expanding a ceiling is a separate reviewed product change with
handler and authorization tests.

The registry is platform configuration, not Agent Class configuration. Users
customize classes by selecting capabilities; they cannot invent an authority
operation that no MCP handler implements.

## MCP tool definitions

Authority metadata must be colocated with each MCP tool definition. The target
shape is conceptually:

```python
ToolDefinition(
    name="architect_engineer_message",
    description="Send a message to an Engineer.",
    input_schema={...},
    authority=AuthoritySpec(
        base_kinds=frozenset({"architect"}),
        requirements=(
            CapabilityRequirement(
                capability="message.engineer",
                minimum_scope="children",
                target_argument="engineer_id",
                target_kind="agent",
            ),
        ),
    ),
)
```

Internal authority metadata is not emitted as arbitrary MCP protocol fields.
The registration layer produces the public MCP spec and retains the internal
descriptor for projection and dispatch checks.

Collection-returning tools may additionally declare `result_kind` and
`result_paths`. The common result gate filters those paths through the same
scope resolver and fails closed if the declared JSON contract is missing or
malformed. Aggregate tools that cannot yet be filtered safely retain a broader
explicit `minimum_scope` until their result contract is migrated.

### Registration invariants

- Every Torque MCP tool has an authority descriptor.
- Every referenced capability exists.
- Every declared base kind is compatible with the tool surface.
- Stale authority descriptors without a registered tool fail coverage tests.
- Registered tools without authority descriptors fail coverage tests and
  should fail daemon startup in development/test configurations.
- There is no "unmapped means allowed" behavior.

## Runtime enforcement

### Tool projection

`tools/list`, deferred tool search, and every `tools/call` use the same frozen
effective authority snapshot.

A tool is visible when:

1. it belongs to the caller's base-kind surface;
2. every unconditional capability requirement is present at a usable scope;
3. no runtime relationship gate makes the tool categorically unavailable.

`tools/call` repeats the check so stale clients cannot invoke a tool removed
from projection.

### Resource authorization

Projection answers whether a tool can possibly be used. The handler must still
authorize the concrete target after resolving it:

```python
authority.require("message.engineer", target=engineer)
```

A generic authorization service determines the caller-to-target relationship
and compares it with the effective maximum scope. Read/list handlers use the
same service to filter rows.

Existing handler ownership checks remain in place until migrated. During
migration, effective authority and the legacy check are intersected; the ACL
must never broaden behavior accidentally.

### Conditional operations

Some tools require additional authority only for particular arguments or
records, such as accepting rather than proposing a decision or setting
`ack_required=true`. Prefer splitting materially different operations into
separate tools. Where splitting is impractical, the handler calls
`authority.require()` for the conditional capability before the side effect.

Mixed-purpose inbox/reply tools should be split or row-filtered. A tool must
not expose Engineer threads merely because it also handles permitted Architect
peer threads.

## Effective authority snapshot

Agent Class launch freezes a self-contained authority snapshot:

```yaml
effective_authority:
  schema_version: 1
  base_kind: architect
  acl_mode: allow
  capabilities:
    self.read: self
    task.read: group
    message.engineer: children
  visible_tools:
    - torque_context
    - architect_task_list
    - architect_engineer_message
  capability_registry_version: 1
  capability_registry_hash: sha256:...
  tool_registry_hash: sha256:...
```

The snapshot, not live YAML or a generated Agent Profile lookup, governs the
session. Assignment changes apply on next launch/relaunch. Preview compares the
next compiled snapshot with the frozen snapshot to show changes.

## Prompt assembly

Prompt assembly is generic and compositional:

1. universal Torque platform contract;
2. base-kind runtime contract;
3. user-authored structured class prompt;
4. generated effective-authority summary;
5. dynamic session context and operating settings;
6. a final immutable reminder that prompt prose cannot broaden MCP authority.

The class prompt remains:

```yaml
prompt:
  identity: ...
  job: ...
  boot_checklist: [...]
  operating_guidelines: [...]
  tool_guidance:
    - when_capability: task.read
      text: ...
```

`tool_guidance` selectors are evaluated generically against the frozen
authority/tool surface. They control whether guidance text is rendered; they
never grant authority.

The prompt renderer must not synthesize class behavior from named classes or
hardcoded capability bundles. It may render a factual capability/scope summary
from effective authority. Built-in-specific identity, workflow, and boot text
belongs in built-in YAML.

Capabilities govern Torque MCP access only. They do not determine whether
custom instructions, behavior overlays, lifecycle warnings, or autonomy
settings exist. Those are separate prompt/configuration concerns and remain
subordinate to the immutable authority boundary.

## Built-in classes and semantic tools

Built-in Agent Classes are examples/defaults, not privileged runtime types.
Deleting or renaming a built-in class must not require production code changes.

MCP tools may implement domain-specific safe operations, but must not be named
or gated by an Agent Class identity. Existing surfaces should migrate toward
semantic operation names, for example:

- Steward operating brief -> `architect_group_health_brief` (complete)
- product task proposal wrappers -> `architect_task_propose`
- product proposed-decision wrappers -> `architect_decision_propose`

No tool handler may test class id, generated profile id, archetype, or metadata
marker to decide authority. It checks capabilities and target scope.

## Removed concepts

Schema v5 and the new runtime do not use:

- status contracts;
- Agent Class `policy:`;
- `class-policy-*` generated Agent Profiles;
- capability buckets;
- restriction buckets;
- separate ACL action keys;
- exact-tool grants;
- tool-family globs;
- raw Agent Profile grant/deny atoms as an Agent Class authoring surface;
- class-specific prompt branches;
- class-specific MCP projection exceptions.

Obsolete authority fields are rejected as unknown/legacy schema input. They are
not normalized into a second runtime authority path.

## Migration plan

### Phase 1: registry foundation

- Introduce `CapabilityDefinition`, `ToolDefinition`, `AuthoritySpec`, and
  `CapabilityRequirement`.
- Establish the canonical capability catalog and scope ceilings based on
  currently enforced behavior.
- Move all current MCP tool mappings beside tool registration.
- Add a fail-closed coverage audit for all registered tools.
- Compare registry output against captured pre-migration fixtures in tests only.

### Phase 2: ACL evaluator and snapshots

- Add schema v5 validation for `acl.mode + acl.rules`.
- Implement allow and deny evaluation against base-kind ceilings.
- Produce frozen effective-authority snapshots and previews.
- Add deterministic registry hashes.
- Migrate built-in YAML to schema v5.

### Phase 3: MCP projection

- Project eager tools, deferred tools, and tool search from effective authority.
- Recheck effective authority on every call.
- Remove exact/family precedence and unmapped-tool fallback.
- Keep legacy resource checks as additional restrictions.

### Phase 4: scoped resource authorization

- Introduce the generic caller-to-resource scope resolver.
- Migrate capabilities in bounded families: messaging, tasks, planning,
  decisions, journals/memory, Thinking/Idea Briefs, agent management,
  worktrees, and global administration.
- Add list filtering and concrete-target denial tests at each scope.
- Expose broader scopes only after the underlying handler supports them.

### Phase 5: prompt and UI

- Replace restricted Architect prompt synthesis with generic composition.
- Render capability/scope facts from the frozen snapshot.
- Update the editor to select mode, capability, and supported scope.
- Show affected tools as derived read-only information.
- Show next-launch authority diffs.

### Phase 6: legacy removal

- Remove Agent Class policy/profile compilation.
- Remove capability and restriction buckets.
- Remove separate action aliases and central tool mappings.
- Remove old Agent Profile authority assignment, storage, and UI.
- Remove class-specific wrapper names and historical fixtures where practical.

## Validation and doctor checks

Torque must report:

- registered tools without authority metadata;
- authority metadata for nonexistent tools;
- unknown capabilities;
- unsupported capability/base-kind combinations;
- missing or unsupported scopes;
- duplicate ACL rules;
- authority changes between assigned and frozen launch snapshots;
- built-in classes that use a path unavailable to custom classes;
- production code that recognizes built-in class ids outside migration code.

## Test requirements

The implementation is complete only when tests cover:

- allow mode starts empty and grants only listed capabilities;
- deny mode starts at the base-kind ceiling and removes/narrows only rules;
- scoped allow boundaries;
- scoped deny boundaries;
- invalid scopes and base-kind escalation;
- every registered MCP tool has authority metadata;
- stale authority metadata is rejected;
- hidden tools remain denied when called by a stale client;
- list handlers filter self/children/group/global resources correctly;
- conditional capabilities are checked before side effects;
- two differently named classes with identical prompt/ACL YAML compile to the
  same authority and prompt structure;
- no prompt or runtime branch depends on a built-in class id;
- running sessions retain frozen authority after YAML changes;
- built-in classes use only public schema and registry behavior.

## Acceptance criteria

- Users can understand class authority from one mode and one capability rule
  list.
- Agent Class capabilities control Torque MCP projection and concrete resource
  authorization, not merely previews or prompt prose.
- No production Agent Class path checks a named class, archetype, or generated
  profile id.
- No Torque MCP tool can be registered without explicit authority metadata.
- No exact tool or family rule can bypass semantic capability requirements.
- The runtime has one frozen Agent Class authority snapshot and no generated
  Agent Profile policy layer.
- Built-in and custom classes are operationally indistinguishable given the
  same base kind, prompt, and ACL.
