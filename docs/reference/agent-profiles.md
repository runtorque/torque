# Agent Profiles

Agent Profiles are a capability-policy layer over Torque's existing runtime
agent kinds. They are **not** new runtime kinds: every profile declares one of
`architect`, `engineer`, or `worker` as its `base_kind`, and validation requires
its grants to stay inside that base-kind ceiling.

Wave 1 is dry-run only. Loading and doctor validation do not hide tools, deny
MCP calls, change dispatch, or alter prompts. Future waves will use the same
validated profile data for server-side projection/enforcement and auditable
effective snapshots.

## Config paths

Torque ships built-in definitions in `torque/builtin_agent_profiles/`:

- `full-architect.yaml`
- `full-engineer.yaml`
- `full-worker.yaml`
- `product-manager-draft.yaml`

Project profile definitions live in:

```text
.torque/agent_profiles/*.yaml
```

The project directory is allow-listed in `.gitignore` like actions, roles, and
specializations so teams can review profile policy in Git. There is no profile
assignment UI and no DB assignment/effective-snapshot/audit persistence in Wave
1.

## YAML shape

```yaml
id: product-manager-draft
version: "1"
base_kind: architect
display_name: Product Manager (draft)
description: Draft architect-derived profile; dry-run only in Wave 1.
lifecycle: draft
grants:
  - observe.board_summary
  - planning.area_read
  - decision.create_proposed
denies:
  - agent.hire_engineer
  - task.dispatch
policy:
  base_kind: architect
  scope:
    planning_visibility: broad same-group Areas/Initiatives reads
  communication:
    engineer_worker_messages: deny by default
  spawn:
    dispatch: deny
metadata:
  archetype: product_manager
```

Do not use `profile`, `agent_profile`, `runtime_profile`, or similar fields in
Agent Profile YAML. `AgentCell.profile` is the legacy terminal/runtime profile
label and is intentionally separate from future `agent_profile_id` /
`agent_profile_version` assignment fields.

## Validation

`torque.agent_profiles` validates definitions for:

- malformed YAML or non-mapping documents;
- missing `id`, `version`, or invalid `base_kind`;
- unknown profile fields, especially fields that confuse Agent Profiles with
  `AgentCell.profile`;
- unknown capability atoms;
- grants outside the declared base-kind ceiling;
- known built-in profile/base-kind mismatches;
- Product Manager grants for dangerous execution/admin capabilities such as
  hire, dispatch, merge, deploy, profile admin, and broad engineer/worker
  messaging.

The built-in full profiles grant exactly their base-kind ceiling by construction,
which preserves current behavior until a future assignment/enforcement wave.

## Dry-run preview data

`dry_run_profile_preview(profile)` returns deterministic preview data suitable
for tests and future UI/API surfaces:

- base kind, profile id/version, lifecycle, and built-in flag;
- granted capabilities with category/risk metadata;
- high-risk capabilities in the base-kind ceiling that the profile denies by
  omission;
- communication/spawn/scope/audit policy summaries;
- projected tool-category allow/deny summaries;
- `runtime_enforcement: not_enabled_wave_1_dry_run_only`.

`torque doctor` includes an `[agent_profiles]` section and fails the
`agent_profiles_valid` check when project or built-in profile definitions are
invalid.
