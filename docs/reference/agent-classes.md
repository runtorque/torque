# Agent Classes

Agent Classes are the user-facing template layer over Torque's existing runtime
kinds: `architect`, `engineer`, and `worker`. They are **not** arbitrary new
runtime kinds. Each Agent Class declares a `base_kind` and references exactly
one Agent Profile. Agent Profile remains the enforcement layer for MCP/capability projection.

Agent Classes are intentionally narrow:

- desired assignment fields are `agent_class_id` / `agent_class_version`;
- effective launch snapshots are `effective_agent_class_*`;
- Agent Profiles still use `agent_profile_id` and `effective_agent_profile_*`;
- `AgentCell.profile` remains the legacy terminal/runtime profile label and is
  not used for Agent Classes.

## Config paths and authoring store

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

The trusted browser/server authoring API persists custom classes by validated,
atomic writes to this same project YAML store. It does not write to SQLite and
does not mutate running sessions. Save, archive/disable, and delete responses
include a `storage` object with `kind: "project_yaml"`, the exact `path` when a
file is touched, `config_glob: ".torque/agent_classes/*.yaml"`, and
`mutates_running_sessions: false`.

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
base kind/version do not match, duplicate/unsafe IDs, missing/unsafe
`display_name`, unsafe version tokens, raw MCP/tool/capability fields (`tools`,
`mcp_tools`, `grants`, `denies`, `tool_categories`, etc.) at the top level or
nested under metadata, and ambiguous `profile` / `profile_id` /
`agent_profile_id` fields that could be confused with `AgentCell.profile`.
Draft classes must be explicit scratch-only and must not claim live dogfood
approval.

The authoring API accepts a few UI aliases and normalizes them before writing
YAML:

- `title` / `display_title` → `display_name`
- `instructions` / `class_instructions` / `class_prompt` → `prompt`
- `label`, `icon`, `badge`, `color` → `metadata.ui.*`

Custom classes may be archived/disabled by setting `metadata.archived: true`
through `agent_class_archive` / `agent_class_disable`. Archived classes stay
visible in list/preview responses with `status: "archived"` and warnings, but
assignment and launch-by-class reject them.

## Trusted server/browser command contract

All commands use the existing trusted `/internal/cmd` browser/server surface.
They are not MCP tools and should not be exposed to worker/engineer/architect
runtime sessions.

### Validate an unsaved draft

```json
{
  "cmd": "agent_class_validate",
  "base_dir": "/path/to/project",
  "agent_class": {
    "id": "release-architect",
    "version": "1",
    "base_kind": "architect",
    "display_name": "Release Architect",
    "agent_profile_ref": {"id": "full-architect", "version": "1"},
    "prompt": "Additive class context only."
  }
}
```

Response shape:

```json
{
  "type": "agent_class_validation",
  "schema_version": 2,
  "valid": true,
  "ok": true,
  "agent_class": {
    "id": "release-architect",
    "source": "project",
    "custom": true,
    "status": "full",
    "agent_profile_ref": {"id": "full-architect", "version": "1"},
    "agent_profile": {"id": "full-architect", "status": "full"},
    "prompt_summary": {"has_prompt": true, "char_count": 28},
    "restrictions": ["Agent Profile remains the MCP/capability enforcement layer."],
    "external_connector_caveat": "External connector exposure is not governed or enforced by Agent Classes in Wave 6B; manage connector access separately."
  },
  "issues": [],
  "errors": [],
  "warnings": [],
  "storage": {"kind": "project_yaml", "config_glob": ".torque/agent_classes/*.yaml"}
}
```

Invalid drafts return the same shape with `valid: false` and structured
`errors[]` items (`severity`, `code`, `message`, optional `path` and
`profile_id`). Nothing is written.

### Create/update/archive/delete

```json
{"cmd": "agent_class_create", "base_dir": "/path/to/project", "agent_class": {...}}
{"cmd": "agent_class_update", "base_dir": "/path/to/project", "agent_class": {...}}
{"cmd": "agent_class_save", "base_dir": "/path/to/project", "mode": "save", "agent_class": {...}}
{"cmd": "agent_class_archive", "base_dir": "/path/to/project", "class_id": "release-architect"}
{"cmd": "agent_class_disable", "base_dir": "/path/to/project", "class_id": "release-architect"}
{"cmd": "agent_class_delete", "base_dir": "/path/to/project", "class_id": "release-architect"}
```

Create refuses an existing custom class; update refuses a missing custom class;
save upserts. Built-in class ids are read-only and cannot be overwritten,
archived, or deleted from project config. Successful mutation responses include:

```json
{
  "type": "agent_class_save",
  "schema_version": 2,
  "ok": true,
  "operation": "created",
  "agent_class": {},
  "classes": [],
  "registry_issues": [],
  "storage": {"kind": "project_yaml", "path": "/path/to/project/.torque/agent_classes/release-architect.yaml"},
  "audit": {"event": "custom_class_created", "mutates_running_sessions": false}
}
```

### List/preview/status/audit

```json
{"cmd": "agent_class_list", "base_dir": "/path/to/project"}
{"cmd": "agent_class_preview", "class_id": "release-architect", "base_dir": "/path/to/project"}
{"cmd": "agent_class_status", "agent_id": "agent-id"}
{"cmd": "agent_class_audit", "agent_id": "agent-id", "limit": 50}
```

`agent_class_list` returns built-in and project classes with source/lifecycle
flags: `source` (`builtin` or `project`), `source_path`, `builtin`, `custom`,
`lifecycle`, `scratch_only`, `archived`/`disabled`, `status`, warnings, the
compact class→profile preview, restrictions, prompt summary, and the external
connector caveat. `agent_class_preview` can preview archived classes so the UI
can show why launch is disabled.

### Launch/create from class

There are two supported backend paths:

1. Pass `agent_class_id` (or `class_id`) to existing trusted create commands:
   `add_architect`, `add_engineer`, `add_worker`, or worker task dispatch with
   `create_agent: true`.
2. Use the explicit command:

```json
{
  "cmd": "create_agent_from_class",
  "class_id": "release-architect",
  "name": "Release Architect",
  "group": "Torque"
}
```

`agent_class_launch` is an alias for `create_agent_from_class`. If `kind` is
provided, it must match the saved class `base_kind`; otherwise the class base
kind selects the base create path. Missing, invalid, archived, or incompatible
classes fail with `type: "error"` and a stable `code` such as
`invalid_agent_class` or `agent_class_base_kind_mismatch`.

Successful create responses include `agent_class_status` and
`agent_profile_status`; the explicit launch command wraps the create result:

```json
{
  "type": "agent_class_launch",
  "schema_version": 1,
  "base_kind": "architect",
  "agent": {
    "id": "agent-id",
    "kind": "architect",
    "agent_class_status": {
      "effective_class_id": "release-architect",
      "effective_class_version": "1",
      "next_launch_profile_id": "full-architect",
      "pending_next_launch": false
    },
    "agent_profile_status": {
      "effective_profile_id": "full-architect",
      "effective_profile_version": "1"
    }
  },
  "agent_class": {},
  "storage": {"mutates_running_sessions": false, "launch_boundary": "new_agent"}
}
```

When no class is selected, the existing default Architect/Engineer/Worker
behavior is unchanged: Torque freezes the implicit `default-{kind}` class and
the full base-kind Agent Profile at launch.

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
