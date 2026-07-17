# Agent Classes

Agent Classes are Torque's operator-facing configuration for an agent's purpose,
prompt, and Torque MCP authority. Every class uses one generic runtime path;
built-in classes are ordinary YAML definitions and receive no class-specific
code branches.

Class authority is authored as a generic ACL.

The runtime base kinds remain `architect`, `engineer`, and `worker`. A class
selects one base kind and can only narrow that kind's platform authority ceiling.
It cannot create a new runtime kind or grant authority that the base kind does
not implement.

## Config paths

Built-in definitions live in `torque/builtin_agent_classes/`. Project classes
live in:

```text
.torque/agent_classes/*.yaml
```

Project class YAML is versioned configuration. SQLite stores desired assignment,
frozen effective snapshots, and audit history; it is not the class definition
source.

## Schema v5

```yaml
agent_class_schema_version: 5
id: planning-architect
version: "1"
base_kind: architect
display_name: Planning Architect
description: Read product context and draft bounded proposals.
lifecycle: stable

prompt:
  identity: >
    You are a grounded product-planning partner for Torque.
  job: >
    Read current context and produce bounded proposals within visible authority.
  boot_checklist:
    - Confirm class, group, and visible tools.
    - Read projected context before proposing changes.
  operating_guidelines:
    - Separate observations, inferences, options, risks, and non-goals.
    - Treat proposals as non-binding until accepted by an authorized actor.
  tool_guidance:
    - when_capability: task.propose
      text: Use task proposal tools only for non-dispatched proposal artifacts.

acl:
  mode: allow
  rules:
    - scope: self
      capabilities:
        - self.read
        - task.propose
    - scope: group
      capabilities:
        - board.read
    - capability: help.read
```

The only authority fields are `acl.mode` and `acl.rules`.

- `mode: allow` grants only the listed capabilities.
- `mode: deny` starts from the base-kind ceiling and removes the listed
  capabilities or narrows their maximum scope.
- A single-capability rule has `capability` and, for scoped capabilities,
  `scope`.
- Repeated scoped rules may instead be grouped as `scope` plus a non-empty
  `capabilities` list. Flat and grouped rules may coexist in one ACL.
- Grouping is authoring syntax only. Torque expands it to the same canonical
  one-rule-per-capability representation before validation, snapshots, tool
  projection, and call-time enforcement.
- A grouped rule cannot contain `capability`, and a flat rule cannot contain
  `capabilities`. Duplicate capability IDs are rejected across both forms.
- Unscoped capabilities use the flat form without `scope`.
- The scope vocabulary is `self`, `children`, `group`, and `global`.
- Validation rejects a capability unavailable to the base kind or a scope wider
  than the capability's implemented ceiling.

There is no Agent Class `policy`, generated policy profile, capability bucket,
restriction bucket, exact-tool grant, tool-family glob, or raw grant/deny atom.
Obsolete top-level fields such as `policy`, `capabilities`,
`capability_buckets`, and `restriction_buckets` are rejected rather than
normalized into a second authority path. The only accepted `capabilities` key
is the grouped authoring form inside an `acl.rules` item.

Prompt `tool_guidance` selectors use canonical capability IDs. Guidance is
included only when that capability exists in the frozen effective authority.
Prompt prose never grants authority.

## MCP enforcement

Each Torque MCP tool maps to one or more canonical capability requirements.
The frozen Agent Class authority controls:

1. whether the tool is projected in `tools/list` and tool search;
2. whether a direct call passes call-time authorization; and
3. where implemented, whether the concrete target resource is within the
   caller's effective scope.

A registered Torque MCP tool without authority metadata fails coverage checks.
External connector tools are governed separately and are not controlled by the
Agent Class ACL.

## Assignment and launch

Assignment changes desired state only. Running sessions retain their current
frozen authority until the next launch or relaunch.

At launch Torque resolves the desired class, or `default-{kind}` when no class
is explicitly assigned, and freezes one `effective_agent_class_snapshot`. That
snapshot contains the normalized ACL, compiled `effective_authority`, registry
hashes, prompt data, class identity, lifecycle, warnings, and snapshot hash.
It is the Agent Class MCP enforcement source.

There is no separate authority profile assignment or policy snapshot. Agent
Class assignment and the frozen `effective_agent_class_snapshot` are the only
class-authority path.

## Built-ins

Torque currently ships default classes plus Product Manager, Creative, and
Torque Steward examples. Their identity, workflows, prompt guidance, and ACLs
come entirely from YAML. Runtime code must not test their ids, metadata flags,
or generated profile ids.

Renaming or deleting a non-default built-in must not require production code
changes. Domain-specific MCP tools may exist, but authorization is based on
capabilities and target scope, never class identity.

## Trusted commands

The trusted browser/server surface supports `agent_class_list`,
`agent_class_preview`, `agent_class_validate`, `agent_class_create`,
`agent_class_update`, `agent_class_archive`, `agent_class_delete`,
`agent_class_assign`, `agent_class_clear`, `agent_class_status`,
`agent_class_audit`, and `create_agent_from_class`.

List and preview responses expose schema-v5 class data, the capability catalog,
normalized ACL, authority summary, compiled effective-authority preview, prompt
summary, apply state, and warnings. They do not expose generated profiles,
internal class policies, or bucket compatibility fields.

## Prompt composition

Torque builds the base-kind prompt first, then appends the class's structured
prompt sections and a factual summary of the frozen capability/scope authority.
Default classes have no extra prompt sections.

The renderer is generic. Class-specific behavior belongs in class YAML, not in
`if class_id == ...` branches or semantic metadata checks.

Action templates can inspect compact class context through
`torque.agent.agent_class`.

## Validation and doctor

Validation rejects unknown fields, unsafe ids/versions, invalid base kinds,
invalid lifecycle/draft metadata, raw MCP tool fields, legacy authority fields,
invalid prompt sections, unknown capability selectors, and invalid ACL rules.

`torque doctor` reports Agent Class validation, assignment/audit state, frozen
snapshot state, and capability registry coverage.
