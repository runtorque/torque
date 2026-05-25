# Role and specialization taxonomy plan

Status: implemented from the approved TORQUE:447 research artifact.

Source artifact: `~/.torque/attachments/TORQUE:447/torque-roles-and-specializations-plan.md` (Courier-approved on 2026-05-18).

## Decision summary

Torque keeps **specializations** and **worker roles** as separate concepts.

- Specializations are persistent Engineer attributes and routing hints. They shape Engineer prompt preambles and help Architects assign work.
- Worker roles are dispatch-time Worker launch and preamble presets. They shape how an individual Worker approaches one task or stream.
- The project provides a one-to-one default mapping so routing stays easy to remember without collapsing the two runtime concepts.

## Specialization taxonomy

| Slug | Scope | Typical task signals |
|---|---|---|
| `ui-ux` | Operator-facing webview and desktop UI: `static/js/*`, `static/style.css`, `webview.html`, board/cards, modals, panels, canvas/grid, and UI regression work. | `ui`, `frontend`, `agent-panel`, `modals`, `canvas`, `grid`, rerender state, focus/scroll/caret preservation |
| `orchestration-core` | Python daemon orchestration: `torque/server.py`, `torque/state.py`, Architect/Engineer flows, MCP tools, dispatch, board, events, digests, and journals. | `backend`, `mcp`, `architect`, `engineer`, `dispatch`, board ownership/scoping, digest/journal behavior |
| `runtime-pty` | Terminal/session runtime: supervised PTY, embedded terminal streaming, adapters, reconnect, boot timing, and provider send paths. | `pty`, `runtime`, `terminal`, `boot-doa`, reconnect/session resume, provider adapter issues |
| `desktop-shell` | Native desktop shell: Tauri, pywebview, detached windows/panels, capability configuration, and desktop/browser parity. | `tauri`, `desktop`, `window`, `panel`, macOS menus/quit/activation, native capability changes |
| `worktree-release` | Git worktrees and release flow: lifecycle, checkpoints, rebase/merge, branch boundaries, review gates, and cleanup. | `worktree`, `rebase`, `merge`, `review-gate`, `checkpoint`, PR cleanup, branch-boundary issues |
| `prompts-config` | Prompt/config surfaces: actions, roles, specializations, templates, system prompts, shared memory, and prompt previews. | `actions`, `roles`, `specializations`, `prompts`, `templates`, memory prompt blocks |
| `quality-observability` | Tests, diagnostics, observability, doctor checks, logs, metrics, health/debug surfaces, and regression harnesses. | `tests`, `observability`, `doctor`, `metrics`, `logs`, `health`, regression coverage |

## Worker role mapping

| Specialization | Default worker role file | Purpose |
|---|---|---|
| `ui-ux` | `.torque/roles/ui-worker.yaml` | Frontend/UI worker preamble. |
| `orchestration-core` | `.torque/roles/orchestration-worker.yaml` | Server/MCP/dispatch worker preamble. |
| `runtime-pty` | `.torque/roles/runtime-worker.yaml` | Terminal/session/runtime worker preamble. |
| `desktop-shell` | `.torque/roles/desktop-worker.yaml` | Native desktop/windowing worker preamble. |
| `worktree-release` | `.torque/roles/release-worker.yaml` | Worktree/merge/release worker preamble. |
| `prompts-config` | `.torque/roles/prompts-worker.yaml` | Actions/roles/prompts/config worker preamble. |
| `quality-observability` | `.torque/roles/quality-worker.yaml` | Tests/diagnostics/observability worker preamble. |

Role files intentionally set only `name`, `display_name`, `description`, `preamble`, and `priorities`. Provider, model, command, permissions, and worktree defaults remain controlled by group settings, actions, or explicit dispatch overrides.

## Engineer retrofit mapping

The TORQUE:447 audit found two current Torque engineers and recommended ordered specialization arrays:

- Panelsmith: `['ui-ux', 'desktop-shell', 'quality-observability']`
- Courier: `['orchestration-core', 'runtime-pty', 'worktree-release', 'prompts-config', 'quality-observability']`

The first slug is primary. The mapping is evidence-backed by retained board labels, journal entries, and current task history in the live SQLite state.

## Version-control invariant

Project-local Torque configuration is versioned when it shapes future dispatch behavior. The repository `.gitignore` intentionally ignores runtime `.torque/*` by default, then allow-lists:

```gitignore
!.torque/actions/
!.torque/actions/**
!.torque/roles/
!.torque/roles/**
!.torque/specializations/
!.torque/specializations/**
```

Runtime-generated worktrees, prompt files, findings, sketches, and other local `.torque/` artifacts stay ignored unless a future task explicitly allow-lists them.

## Validation expectations

- `RoleManager.list_roles(base_dir='.')` should discover all seven project worker roles.
- `SpecializationManager.list_specializations(base_dir='.')` should discover all seven project specializations.
- `git check-ignore -v .torque/roles/ui-worker.yaml .torque/specializations/ui-ux.yaml` should emit no output, confirming the allow-list works.
