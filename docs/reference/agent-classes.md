# Agent Classes

Agent Classes are the normal operator-facing authoring, selection, assignment,
and launch object for Torque agents. Runtime substrate still uses the internal
base kinds `architect`, `engineer`, and `worker`; Agent Classes are **not**
arbitrary runtime kinds. Class authority is authored as a generic ACL over
Torque MCP tools/actions. Torque still projects the resolved ACL into the
existing Agent Profile-compatible runtime snapshot fields for launch-time MCP
enforcement, but that is an internal/backcompat implementation detail.

Class-first invariants:

- desired assignment fields are `agent_class_id` / `agent_class_version`;
- effective launch snapshots are `effective_agent_class_*`;
- the frozen runtime MCP projection is still stored in `effective_agent_profile_*`
  for compatibility with existing session enforcement;
- direct Agent Profile assignment remains supported only as Advanced/Internal
  backcompat, not the default product flow;
- `AgentCell.profile` remains the legacy terminal/runtime profile label and is
  not used for Agent Classes.

## Config paths and authoring store

Torque ships built-in Agent Classes in `torque/builtin_agent_classes/`:

- `default-architect.yaml` → wraps `full-architect@1`;
- `default-engineer.yaml` → wraps `full-engineer@1`;
- `default-worker.yaml` → wraps `full-worker@1`;
- `creative-architect.yaml` → schema v4 ACL/data-defined prompt for the
  built-in Creative class;
- `product-manager.yaml` → schema v4 ACL/data-defined prompt for the built-in
  Product Manager class;
- `torque-steward.yaml` → schema v4 ACL/data-defined prompt for the built-in
  Torque Steward class.

Project Agent Class definitions live in:

```text
.torque/agent_classes/*.yaml
```

This path is reviewed in Git like `.torque/actions/`, `.torque/roles/`,
`.torque/specializations/`, and `.torque/agent_profiles/`. Class YAML is the
reviewed source of identity, prompt, and ACL authority. SQLite effective
snapshots/audit and preview/status payloads carry the frozen runtime projection.

## YAML shape

Schema v4 is ACL-first:

```yaml
agent_class_schema_version: 4
id: product-manager
version: "3"
display_name: Product Manager
lifecycle: stable
runtime:
  base_kind: architect
prompt:
  addendum: |
    Additive class instructions. Class-specific behavior lives here, not in
    Python branches.
acl:
  mode: allow # allow | deny
  allow:
    - capability: self_context      # legacy bucket alias during migration
    - action: planning.area.read
      scope: group
    - family: architect_product_*
warnings:
  - External connectors are managed separately.
```

ACL entries may select exact MCP tools, tool families, stable action keys, or
legacy capability bucket aliases while migration continues. ACL modes are exclusive:
`mode: allow` uses only `allow` entries and denies everything else by omission;
`mode: deny` uses only `deny` entries from the base-kind ceiling.
Cross-base-kind escalation is impossible: an Architect class
can select only Architect-ceiling tools/actions, an Engineer class only
Engineer-ceiling tools/actions, and so on. Resource-touching actions use the
simple scope vocabulary `self`, `children`, `group`, and `global`; relationship
phrases such as hired-by-self or owned-workers compile to `children`.

Legacy class YAML with top-level `base_kind`/`agent_profile_ref`,
`capabilities.buckets`, `capabilities.restrictions`, or `policy.mode` is still
accepted as migration/backcompat input, but normal authoring and built-in class
YAML should use `acl:`. The old operator-readable `policy.scope`,
`policy.communication`, `policy.spawn`, and `policy.audit` fields do not enforce
MCP access and should not be used for new classes.

Validation rejects unknown base kinds, missing/unsafe ids, unsafe version tokens,
missing/unsafe display names, profile refs whose base kind/version do not match,
raw MCP/tool fields (`tools`, `mcp_tools`, `tool_picker`, etc.) outside ACL
entries, raw Agent Profile capability-atom grants/denies in Agent Class YAML/API,
and ambiguous `profile` / `profile_id` / `agent_profile_id` fields. ACL entries
outside the base-kind ceiling are rejected.

Draft classes must set `draft.scratch_only: true` and must not claim live dogfood
approval.

## Trusted server/browser command contract

All commands use the trusted `/internal/cmd` browser/server surface. They are
not MCP tools and should not be exposed to worker/engineer/architect runtime
sessions.

`agent_class_list`, `agent_class_preview`, `agent_class_validate`,
`agent_class_create`, `agent_class_update`, `agent_class_archive`,
`agent_class_delete`, `agent_class_assign`, `agent_class_clear`,
`agent_class_status`, `agent_class_audit`, and `create_agent_from_class` remain
supported. Class responses use schema version 4 and include class-first fields
needed by the UI:

- `primary_display_name` / `primary_identity_label` — the primary UI identity
  label (for example `Product Manager`);
- `secondary_base_kind_label` / `secondary_base_kind_metadata` — internal base
  kind metadata (for example `Architect-derived`);
- `purpose` / `description` — operator-language class purpose;
- `acl` and `authority_summary` — ACL mode, allowed or denied actions, tool
  families, exact tools, selected legacy capabilities, high-risk grants, and
  unavailable high-risk capabilities;
- `capability_bucket_selection`, `restriction_bucket_selection`,
  `operator_access_summary`, `capability_buckets`, and `restriction_buckets` —
  migration/backcompat summaries when legacy bucket aliases are used;
- `authoring_contract`, `capability_bucket_catalog`, and
  `restriction_bucket_catalog` — trusted API data the UI can use for migration
  bucket create/edit/validate flows;
- `internal_policy`, `agent_profile`, `internal_profile`, and
  `compiled_profile` — Advanced/Internal runtime-projection diagnostics retained
  for backcompat;
- desired/effective/pending status fields: `assigned_*`, `effective_*`,
  `next_launch_*`, `pending_next_launch`, `next_launch_primary_identity_label`,
  and `apply_state` / relaunch-required state;
- `warnings` plus `external_connector_caveat`.

Successful `create_agent_from_class` responses use `schema_version: 4`, return
`agent_class` preview, and include both `agent_class_status` and
`agent_profile_status` on the created agent. Passing both `agent_class_id` and a
direct profile assignment in a launch payload is rejected as ambiguous.

## Assignment and launch snapshots

Assignment only changes desired state and writes audit rows. Running sessions
keep their frozen effective Agent Class and Agent Profile-compatible runtime
projection snapshots. At the next launch/relaunch, Torque freezes the desired
class (or implicit `default-{kind}`) and freezes either:

1. the wrapped registry Agent Profile for legacy/default classes, or
2. the generated in-memory runtime projection compiled from the class ACL.

The generated projection is frozen into `effective_agent_profile_snapshot` with
metadata such as `generated_by_agent_class`, compiler version, and the class
id/version. It is not written as `.torque/agent_profiles/*.yaml`.

Direct Agent Profile assignments remain compatible. If an agent has a legacy
direct profile assignment and no desired class, Torque preserves that assignment
and does **not** silently migrate it. Status and doctor warn the operator to set
a desired Agent Class for the class-first next-relaunch flow.

## Product Manager class

The built-in Product Manager class has primary identity label **Product Manager**
(even while lifecycle/status remains draft/restricted). Draft/restricted is a
warning/chip, not part of the name.

Product Manager declares a product-safe ACL in YAML. The selected
capability aliases and tool families match the existing product-safe wrapper
surface: planning reads, proposed decisions, queued product task intake,
selected product-peer wrappers, product-safe user communication, and private
journal. Explicit ACL denials record no hire, dispatch, merge, deploy,
settings/admin, profile-admin, raw tool-picker authority, accepted-decision
authority, or direct engineer/worker messaging.

## Creative class

The built-in Creative class has primary identity label **Creative** and keeps
the stable internal id `creative-architect`. It declares a proposal-only
ideation ACL in YAML. It is an Architect-derived thinking mode, not a new
runtime kind and not an execution authority.

Creative can read same-group product context, Planning
Areas/Initiatives, decisions, relevant recent context, and Thinking artifacts.
It can create/update only its own Scratchpad notes and Mind Maps through
`architect_thinking_*` wrappers. It can draft, refine, park, archive, and
explicitly propose only its own Idea Briefs through
`architect_product_idea_brief_*` wrappers; proposing an Idea Brief is
review-only and never creates tasks, assigns Engineers/Workers, dispatches
work, accepts decisions, merges, or deploys. Other proposal outputs use
existing product-safe wrappers: proposed decisions, queued/unassigned product
task proposals, product-anchored peer messages, and product-scoped user
ask/message paths.

Its allow-mode ACL grants only the approved proposal, Thinking, product-wrapper,
behavior-overlay, communication, and journal surfaces. Hire/Engineer management,
Worker dispatch, execution task control, direct Engineer/Worker messaging,
worktree/merge, deploy/admin/settings, class/profile admin, accepted-decision
authority, raw tool-picker authority, and everything else are denied by omission.
Its prompt addendum instructs the agent to diverge first, converge second,
connect product patterns, propose small shippable slices, state risks/non-goals,
and never treat ideas as accepted plans.

External connector exposure is a known limitation: Agent Classes and Agent
Profiles do **not** enforce external connector governance in Wave 7. Generic
previews, status, and doctor output surface this caveat for draft/restricted
classes, but connector access must be managed separately.

## Torque Steward class

The built-in Torque Steward class has primary identity label **Torque Steward**
and stable internal id `torque-steward`. The communication/journal wave still keeps it lifecycle
`draft` and `draft.scratch_only: true`; it must not be auto-created, auto-run,
or treated as broad user-delegated authority.

Torque Steward is Architect-derived because it will eventually represent the
user's operational wishes for a group, but its ACL remains
conservative. It can read projected self context, board/task
summaries and details, recent events, MCP-call telemetry, Areas, Initiatives,
Decisions, and board-sync state. It can also ask/message the user, read/write
its own private Architect journal, and list/message same-group Architect peers
for coordination and handoff nudges. Its allow-mode ACL does not grant Engineer
management, Worker dispatch, execution task control, direct Engineer/Worker
messaging, worktree/merge, deploy/admin/settings, class/profile admin,
accepted-decision authority, raw tool-picker authority, or other high-risk
operations, so those are denied by omission.

The bundled YAML prompt and generic ACL preview define the Steward as an operations
observer/suggester: summarize health, anomaly, stale/stuck work, missed
handoff, review/fix-loop, and cleanup risks; separate evidence from inference;
recommend the smallest safe next step and the authorized actor. Torque Steward
must not restart, compact, notify, schedule, dispatch, assign, hire, merge,
deploy, edit Agent Classes/Profiles, change settings, accept decisions, or
message/control Engineers or Workers. The only write surfaces in this wave are
communication/journal records: user asks/messages, own-journal entries, and
same-group Architect peer messages. Explicit user-directed powerful actions
remain future reviewed waves that need confirmation, auditability, visibility,
and rollback expectations before enablement.

Wave B adds one deterministic read-only helper,
`architect_steward_operating_brief`, when the caller's projected ACL/capabilities grant
the required read atoms. The helper returns a structured onboarding/operating
brief with:

- observed facts: group/task counts, active workstreams, current asks/gates,
  visible actor/class context, and Help doc references;
- inferred risks: blocked asks, stale handoffs/reviews, missed user-update
  candidates, dangling/unused workers, silent agents/workstreams, unhealthy
  tasks, and visible branch-boundary/merge gates;
- suggested next steps and responsible-actor recommendations, each marked as a
  recommendation with `mutation_performed: false`.

The helper is intentionally not a routing or authority surface. It does not
create/update/move/assign/dispatch tasks, message users/agents, hire/dismiss
Engineers, accept decisions, edit classes/profiles, merge worktrees, deploy, or
restart anything. It is a bounded starting point for Steward answers; the
Steward should still cite visible evidence and use Help docs for Torque concept
explanations.

## Prompt composition

Base-kind system prompts remain primary. If an effective Agent Class has an
additive prompt, Torque appends a compact Agent Class block after the base
prompt. Default/full classes have no prompt addendum so unassigned default
Architect/Engineer/Worker behavior stays compatible.

Action templates can inspect compact class context via
`torque.agent.agent_class` in the Jinja `torque` namespace.

## Doctor

`torque doctor` includes an `[agent_classes]` section with config path, schema
version, validation counts, capability/restriction bucket catalog counts,
assignment/audit counts, launch-pairing enforcement mode, the external connector
caveat, and generic legacy direct-profile warning counts. The JSON report
includes class previews, assignment status, recent audit rows, ACL/runtime
projection details, bucket authoring contract data, and no-silent-migration
warnings.
