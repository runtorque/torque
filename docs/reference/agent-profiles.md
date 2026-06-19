# Agent Profiles

Agent Profiles are a monotonic capability-policy layer over Torque's existing
runtime agent kinds. They are **not** new runtime kinds: every profile declares
one of `architect`, `engineer`, or `worker` as its `base_kind`, and validation
requires grants to stay inside that base-kind ceiling.

Wave 3 adds trusted-user assignment storage and frozen launch/session snapshots.
`AgentCell.profile` remains the legacy terminal/runtime profile label; Agent
Profile assignment uses separate `agent_profile_id` / `agent_profile_version`
fields plus `effective_agent_profile_*` launch snapshot fields.

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
version: "1"
base_kind: architect
display_name: Product Manager (draft)
description: Draft architect-derived profile; enforced only when explicitly used as an effective profile.
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
Agent Profile YAML. Those names are rejected where they risk confusion with
runtime profile fields.

## Validation and preview

`torque.agent_profiles` validates definitions for malformed YAML, missing
identity fields, unknown fields/capabilities, grants outside the declared base
kind, built-in profile/base-kind mismatches, and Product Manager grants for
dangerous execution/admin capabilities.

Preview helpers expose base kind, profile id/version, full/draft/restricted
status, granted capabilities, high-risk denied capabilities, policy summaries,
projected tool-category allow/deny status, and warnings. `product-manager-draft`
previews warn that it is Wave 3 infrastructure-only and that
`architect_peer_inbox` / `architect_reply` remain denied because those surfaces
are mixed-purpose Architect↔Architect / Architect↔Engineer tools.

`torque doctor` includes `[agent_profiles]` validation plus assignment/audit
counts and structured assignment/audit data in the JSON report. The frontend
agent panel shows a compact Agent Profile badge with effective id/version,
restricted/draft warning color, high-risk denied details in the tooltip, and a
pending-next-launch marker when the desired assignment differs from the frozen
effective snapshot.
