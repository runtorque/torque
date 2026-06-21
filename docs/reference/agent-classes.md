# Agent Classes

Agent Classes are the normal operator-facing authoring, selection, assignment,
and launch object for Torque agents. Runtime substrate still uses the internal
base kinds `architect`, `engineer`, and `worker`; Agent Classes are **not**
arbitrary runtime kinds. Agent Profile-compatible policy remains the internal
MCP/capability enforcement layer underneath each effective class.
In short: Agent Class is the primary identity; Agent Profile remains the enforcement layer.

Class-first invariants:

- desired assignment fields are `agent_class_id` / `agent_class_version`;
- effective launch snapshots are `effective_agent_class_*`;
- the frozen internal policy is still stored in `effective_agent_profile_*`;
- direct Agent Profile assignment remains supported as Advanced/Internal policy
  backcompat, not the default product flow;
- `AgentCell.profile` remains the legacy terminal/runtime profile label and is
  not used for Agent Classes.

## Config paths and authoring store

Torque ships built-in Agent Classes in `torque/builtin_agent_classes/`:

- `default-architect.yaml` → wraps `full-architect@1`
- `default-engineer.yaml` → wraps `full-engineer@1`
- `default-worker.yaml` → wraps `full-worker@1`
- `product-manager.yaml` → primary label **Product Manager**, schema v3,
  compiles to generated/internal `class-policy-product-manager@2`

Project Agent Class definitions live in:

```text
.torque/agent_classes/*.yaml
```

This path is reviewed in Git like `.torque/actions/`, `.torque/roles/`,
`.torque/specializations/`, and `.torque/agent_profiles/`. Wave 7B does **not**
write generated internal profiles as project YAML; the class YAML is the reviewed
source, while SQLite effective snapshots/audit and preview/status payloads carry
the generated/internal policy evidence.

## YAML shape

Wave 6 class YAML with top-level `base_kind` and `agent_profile_ref` remains
valid and is treated as `policy.mode: wrap_profile`.

Schema v3 adds class-first identity/runtime/policy sections and a compile mode:

```yaml
agent_class_schema_version: 3
id: product-manager
version: "2"
display_name: Product Manager
lifecycle: draft
identity:
  label: Product Manager
  primary_ui_label: Product Manager
runtime:
  base_kind: architect
  base_kind_label: Architect-derived
  arbitrary_runtime_kind: false
prompt:
  addendum: |
    Additive class prompt. Base-kind prompt remains primary.
policy:
  mode: compile              # compile | wrap_profile
  policy_schema_version: 1
  generated_profile_id: class-policy-product-manager
  generated_profile_version: "2"
  grants:
    - observe.self_context
    - planning.area_read
  denies:
    - task.dispatch
    - worktree.merge
  scope: {}
  communication: {}
  spawn: {}
  audit: {}
capabilities:
  domains: [product_planning]
delegation:
  dispatch_workers: denied
warnings:
  - External connectors are managed separately.
draft:
  scratch_only: true
  approved_for_live_dogfood: false
metadata:
  archetype: product_manager
```

Validation rejects unknown base kinds, missing/unsafe ids, unsafe version tokens,
missing/unsafe display names, profile refs whose base kind/version do not match,
raw MCP/tool fields (`tools`, `mcp_tools`, `tool_picker`, etc.) anywhere, and
ambiguous `profile` / `profile_id` / `agent_profile_id` fields. For schema v3,
`policy.grants` and `policy.denies` are capability atoms, not raw tool names;
grants must stay inside the declared base-kind ceiling. Product Manager-style
classes cannot grant dangerous execution/admin capabilities such as hire,
dispatch, merge, deploy, settings, or profile-admin.

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
supported. Class responses now use schema version 3 and include class-first
fields needed by Wave 7C:

- `primary_display_name` / `primary_identity_label` — the primary UI identity
  label (for example `Product Manager`);
- `secondary_base_kind_label` / `secondary_base_kind_metadata` — internal base
  kind metadata (for example `Architect-derived`);
- `policy.mode` and `internal_policy` — compile/wrap mode, generated/internal
  profile id/version, compiler version, capability counts, projected tool
  categories, denied high-risk capabilities, and snapshot source;
- `agent_profile` / `internal_profile` / `compiled_profile` — advanced internal
  Agent Profile-compatible policy preview; retained for backcompat;
- desired/effective/pending status fields:
  `assigned_*`, `effective_*`, `next_launch_*`, `pending_next_launch`, and
  `next_launch_primary_identity_label`;
- `warnings` plus `external_connector_caveat`.

Successful `create_agent_from_class` responses use `schema_version: 3`, return
`agent_class` preview, and include both `agent_class_status` and
`agent_profile_status` on the created agent. Passing both `agent_class_id` and a
direct profile assignment in a launch payload is rejected as ambiguous.

## Assignment and launch snapshots

Assignment only changes desired state and writes audit rows. Running sessions
keep their frozen effective Agent Class and Agent Profile snapshots. At the next
launch/relaunch, Torque freezes the desired class (or implicit `default-{kind}`)
and freezes either:

1. the wrapped registry Agent Profile (`policy.mode: wrap_profile`), or
2. the generated in-memory Agent Profile-compatible policy
   (`policy.mode: compile`).

The generated policy is frozen into `effective_agent_profile_snapshot` with
metadata such as `generated_by_agent_class`, `policy_schema_version`,
`policy_compiler_version`, and the class id/version. It is not written as
`.torque/agent_profiles/*.yaml` in Wave 7B.

Direct Agent Profile assignments remain compatible. If an agent has a direct
`product-manager-draft` profile assignment and no desired class, Torque preserves
that legacy direct assignment and does **not** silently migrate it to the
Product Manager class. Status and doctor warn the operator to set desired Agent
Class `product-manager` for the class-first next-relaunch flow.

## Product Manager class

The built-in Product Manager class has primary identity label **Product Manager**
(even while lifecycle/status remains draft/restricted). Draft/restricted is a
warning/chip, not part of the name.

Product Manager compiles PM-safe policy from class YAML to the internal generated
profile `class-policy-product-manager@2`. The grants match the existing PM-safe
wrapper surface: planning reads, PM-owned proposed decisions, queued product task
intake, selected product-peer wrappers, PM-safe user communication, and private
journal. It does not grant raw Architect task/decision/peer tools, hire,
dispatch, merge, deploy, settings/admin, profile-admin, raw tool picker
authority, or direct engineer/worker messaging.

External connector exposure is a known limitation: Agent Classes and Agent
Profiles do **not** enforce external connector governance in Wave 7. Previews,
status, and doctor output surface this caveat, especially for PM/draft/restricted
classes, but connector access must be managed separately.

## Prompt composition

Base-kind system prompts remain primary. If an effective Agent Class has an
additive prompt, Torque appends a compact Agent Class block after the base
prompt. Default/full classes have no prompt addendum so unassigned default
Architect/Engineer/Worker behavior stays compatible.

Action templates can inspect compact class context via
`torque.agent.agent_class` in the Jinja `torque` namespace.

## Doctor

`torque doctor` includes an `[agent_classes]` section with config path, schema
version, validation counts, assignment/audit counts, launch-pairing enforcement
mode, the external connector caveat, and legacy direct PM-profile warning counts.
The JSON report includes class previews, assignment status, recent audit rows,
compiled/internal policy details, and no-silent-migration warnings.
