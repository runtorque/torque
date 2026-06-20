# Agent Classes

Agent Classes are the user-facing template layer over Torque's existing runtime
kinds: `architect`, `engineer`, and `worker`. They are **not** arbitrary new
runtime kinds. Each Agent Class declares a `base_kind` and references exactly
one Agent Profile. Agent Profile remains the enforcement layer for MCP/capability projection.

Agent Classes are intentionally narrow in Wave 6B:

- desired assignment fields are `agent_class_id` / `agent_class_version`;
- effective launch snapshots are `effective_agent_class_*`;
- Agent Profiles still use `agent_profile_id` and `effective_agent_profile_*`;
- `AgentCell.profile` remains the legacy terminal/runtime profile label and is
  not used for Agent Classes.

## Config paths

Torque ships built-in Agent Classes in `torque/builtin_agent_classes/`:

- `default-architect.yaml` → `full-architect@1`
- `default-engineer.yaml` → `full-engineer@1`
- `default-worker.yaml` → `full-worker@1`
- `product-manager.yaml` → `product-manager-draft@2` (draft/scratch-only)

Project Agent Class definitions live in:

```text
.torque/agent_classes/*.yaml
```

This path is intended to be reviewed in Git like `.torque/actions/`,
`.torque/roles/`, `.torque/specializations/`, and `.torque/agent_profiles/`.

## YAML shape

```yaml
id: product-manager
version: "1"
base_kind: architect
display_name: Product Manager (draft)
description: Scratch-only Product Manager class over the Architect runtime kind.
lifecycle: draft
agent_profile_ref:
  id: product-manager-draft
  version: "2"
prompt: |
  Optional additive prompt context. The base-kind prompt remains primary.
draft:
  scratch_only: true
  approved_for_live_dogfood: false
metadata:
  archetype: product_manager
```

Validation rejects unknown base kinds, missing profile refs, profile refs whose
base kind/version do not match, duplicate/unsafe IDs, raw MCP/tool/capability
fields (`tools`, `mcp_tools`, `grants`, `denies`, `tool_categories`, etc.), and
ambiguous `profile` fields that could be confused with `AgentCell.profile`.
Draft classes must be explicit scratch-only and must not claim live dogfood
approval.

## Assignment and launch snapshots

Trusted server/browser commands can list, preview, assign, clear, audit, and
show status for Agent Classes. Wave 6B does not add generic Agent Class MCP
assignment tools and does not add a full UI class picker/editor.

Assignment only changes desired state and writes audit rows. Running sessions
keep their frozen effective Agent Class and Agent Profile snapshots. At the next
launch/relaunch, Torque freezes the desired class (or the implicit
`default-{kind}` class for unassigned agents) and freezes the referenced Agent
Profile snapshot at the same launch boundary.

If an explicit desired class is missing, invalid, or incompatible with the
agent's base kind at launch, launch fails visibly instead of silently falling
back. Existing agents without a desired class and without a direct Agent Profile
assignment get the default full base-kind class/profile by construction.

## Product Manager draft caveat

`product-manager` is draft/scratch-only in Wave 6B. It references
`product-manager-draft@2`, preserves the Product Manager wrapper restrictions,
and must not be used for live PM dogfood, Blueprint replacement, dispatch/hire,
merge/deploy, or production product authority without explicit future approval.

External connector exposure is a known limitation: Agent Classes and Agent
Profiles do **not** enforce external connector governance in Wave 6B. Previews
and doctor output surface this caveat, especially for draft/restricted classes,
but connector access must be managed separately.

## Prompt composition

Base-kind system prompts remain primary. If an effective Agent Class has an
additive `prompt`, Torque appends a compact Agent Class block after the base
prompt. Default/full classes have no prompt addendum so unassigned default
Architect/Engineer/Worker behavior stays compatible.

Action templates can inspect compact class context via
`torque.agent.agent_class` in the Jinja `torque` namespace.

## Doctor

`torque doctor` includes an `[agent_classes]` section with config path,
validation counts, assignment/audit counts, launch-pairing enforcement mode,
and the external connector caveat. The JSON report includes class previews,
assignment status, and recent audit rows.
