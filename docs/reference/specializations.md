# Roles and specializations

Torque uses two related, but separate, routing concepts:

- **Specializations** describe persistent Engineer focus areas. They are stored in `.torque/specializations/*.yaml` or `~/.torque/specializations/*.yaml`, can be assigned to Engineers, and inject concise Engineer prompt guidance.
- **Worker roles** describe dispatch-time Worker behavior. They are stored in `.torque/roles/*.yaml` or `~/.torque/roles/*.yaml`, can be referenced by actions or task role fields, and prepend Worker preamble/priorities during dispatch.

## Project specialization taxonomy

| Slug | Use it for | Route examples |
|---|---|---|
| `ui-ux` | Operator-facing webview and desktop UI. | Board/card UI, modals, canvas/grid, agent panel, CSS/JS refactors, focus/scroll/caret preservation, frontend regression tests. |
| `orchestration-core` | Daemon orchestration and workflow semantics. | `server.py`, `state.py`, MCP tools, Architect/Engineer flows, dispatch, board scoping, events, digests, journals. |
| `runtime-pty` | Terminal and session runtime behavior. | iTerm2 bridge, standalone/supervised PTY, provider adapters, worker boot DOA, reconnect/session resume, prompt send timing. |
| `desktop-shell` | Native shell and windowing behavior. | Tauri, pywebview, detached windows/panels, macOS menu/quit/activation, native capability and desktop/browser parity. |
| `worktree-release` | Git worktree and release safety. | Worktree lifecycle, checkpoints, rebase/merge, branch boundaries, review gates, PR cleanup, merge conflict handling. |
| `prompts-config` | Prompt/config surfaces. | Actions, roles, specializations, templates, system prompts, shared memory prompt blocks, prompt preview behavior. |
| `quality-observability` | Tests, diagnostics, and instrumentation. | Regression harnesses, `torque doctor`, logs, metrics, health/debug surfaces, low-noise observability. |

## Default worker role mapping

| Specialization | Worker role |
|---|---|
| `ui-ux` | `ui-worker` |
| `orchestration-core` | `orchestration-worker` |
| `runtime-pty` | `runtime-worker` |
| `desktop-shell` | `desktop-worker` |
| `worktree-release` | `release-worker` |
| `prompts-config` | `prompts-worker` |
| `quality-observability` | `quality-worker` |

Use the specialization slug when routing work to an Engineer. Use the worker role when a task or action should boot a Worker with the matching dispatch preamble.

## Architect usage

When an Architect creates a scoped task, set `suggested_specialization` when one slug clearly matches the work. The hint is non-binding, but Torque surfaces a warning if the assigned Engineer does not carry that specialization. If several slugs apply, pick the primary deliverable. Use `quality-observability` only when tests, diagnostics, metrics, doctor checks, or instrumentation are the main deliverable; do not add it to every task just because tests are required.

If no available Engineer carries the primary specialization, either assign the best-fit Engineer with the warning visible, reassign to a better-fit Engineer, or request a hire when the gap is sustained.

## Engineer usage

Engineers should treat a task's `suggested_specialization` as a routing hint, not a command. If the hint matches the Engineer's specialization set, lean into that preamble and choose the matching Worker role when dispatching a fresh Worker. If the hint is outside the Engineer's set or conflicts with the task description, record the reasoning and either proceed, reassign, or escalate to the hiring Architect or user.

## Current Torque engineer mapping

The approved TORQUE:447 audit recommended these ordered specialization arrays:

- Panelsmith: `ui-ux`, `desktop-shell`, `quality-observability`
- Courier: `orchestration-core`, `runtime-pty`, `worktree-release`, `prompts-config`, `quality-observability`

The first slug is the primary specialization.
