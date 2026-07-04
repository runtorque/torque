# Agent Profiles

Agent Profiles are the legacy/internal MCP projection layer over Torque's existing
runtime agent kinds. They are **not** new runtime kinds: every profile declares
one of `architect`, `engineer`, or `worker` as its `base_kind`, and validation
requires grants to stay inside that base-kind ceiling.

Waves 3-4 add trusted-user assignment storage, frozen launch/session snapshots, and the Wave 4B Product Manager wrapper surface.
`AgentCell.profile` remains the legacy terminal/runtime profile label; Agent
Profile assignment uses separate `agent_profile_id` / `agent_profile_version`
fields plus `effective_agent_profile_*` launch snapshot fields.


## Relationship to Agent Classes

Agent Classes are now the normal operator-facing template/selection layer.
Class authority is authored as ACL YAML and compiled into an internal
Agent Profile-compatible runtime snapshot for MCP projection. Class config lives
in `.torque/agent_classes/*.yaml`; reviewed authored profile config remains in
`.torque/agent_profiles/*.yaml` for Advanced/Internal backcompat only. Agent
Class normal authoring does not expose raw Agent Profile atom grants/denies.

Direct Agent Profile assignment remains trusted-user-only for
Advanced/Internal backcompat. Agent Class assignment is also
trusted-user-only and freezes a class/runtime-projection pair on the next launch,
without mutating running sessions.

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
specializations so teams can review profile policy in Git.

## Assignment and effective snapshots

Assignments are trusted-user-only in v1. Server/browser commands may set the
**desired** assignment (`agent_profile_id` / `agent_profile_version`), but
Architect/Engineer/Worker MCP tools do not expose a profile assignment write.
Agents can propose profile changes through normal review/product channels, not
apply them directly.

Desired assignment is separate from runtime policy:

1. Updating `agent_profile_id` records the desired next profile and writes an
audit event.
2. A running session keeps its current `effective_agent_profile_*` snapshot.
3. On the next launch/relaunch/restart, Torque freezes the desired assignment —
or the default full base-kind profile when no assignment exists — into
`effective_agent_profile_id`, `effective_agent_profile_version`, and
`effective_agent_profile_snapshot`.
4. MCP projection/denial reads only the frozen effective snapshot, so profile
changes do not silently mutate already-running sessions.

Existing/unassigned agents default to the full base-kind profile (`full-worker`,
`full-engineer`, or `full-architect`) with no behavior change. Explicit full
profiles and other explicit effective launch snapshots are the only profile
contexts that affect MCP policy; full profiles preserve current tool
visibility/direct-call compatibility.

## YAML shape

```yaml
id: product-manager-draft
version: "2"
base_kind: architect
display_name: Product Manager (draft)
description: Draft architect-derived profile; enforced only when explicitly used as an effective profile.
lifecycle: draft
grants:
  - observe.board_summary
  - planning.area_read
  - decision.create_proposed
  - decision.update_proposed
denies:
  - agent.hire_engineer
  - task.dispatch
acl:
  mode: allow
  allowed_families:
    - architect_product_*
  denied_tools:
    - architect_tool_search
metadata:
  runtime_enforcement: mcp_projection_when_effective_profile_is_set
```

Do not use `profile`, `agent_profile`, `runtime_profile`, or similar fields in
Agent Profile YAML. Those names are rejected where they risk confusion with
runtime profile fields.

## Validation and preview

`torque.agent_profiles` validates definitions for malformed YAML, missing
identity fields, unknown fields/capabilities, grants outside the declared base
kind, and built-in profile/base-kind mismatches.

Preview helpers expose base kind, profile id/version, full/draft/restricted
status, granted capabilities, high-risk denied capabilities, ACL summaries,
projected tool-category allow/deny status, and warnings. `product-manager-draft`
is retained only as a legacy direct-profile compatibility artifact; class-first
Product Manager assignment should use the Product Manager Agent Class. The draft
profile is not a runtime kind and must not be used for live product authority or
Blueprint replacement.

`torque doctor` includes `[agent_profiles]` validation plus assignment/audit
counts and structured assignment/audit data in the JSON report. It warns for
legacy direct `product-manager-draft` assignments and does not silently migrate
them to the Product Manager class. UI should render profile data as
Advanced/Internal runtime-projection detail behind Agent Class identity; Agent
Class create/edit flows should use the Agent Class ACL UI rather than raw profile
atoms.


## Product Manager Wave 4B scratch smoke

Wave 4B's Product Manager profile is for scratch validation only. Do not move Blueprint, do not create live PM product decisions, and do not use it for real product work. A safe operator smoke should use a disposable profile/session on a separate port, then verify only the wrapper surface:

```bash
TORQUE_PORT=18933 TORQUE_PROFILE=pm-wrapper-scratch make standalone-bg
# In the scratch UI/session only:
# 1. Create or select a scratch Architect in a scratch group.
# 2. Assign product-manager-draft and relaunch that scratch Architect.
# 3. Confirm tools/list exposes architect_product_* wrappers and hides raw
#    architect_peer_*, architect_decision_*, architect_task_*, dispatch, hire,
#    merge, deploy, admin, and profile-admin tools.
# 4. Create a throwaway product task proposal with
#    architect_product_task_propose and confirm it is queued/unassigned with
#    product-proposal and pm-created labels.
# 5. Optionally send a product-peer message only to an explicitly scratch
#    product-profile peer with a product-scope anchor.
```

Stop the scratch daemon from the shell that launched it. Workers running inside
Torque must not run deploy/stop/restart against their parent daemon.
