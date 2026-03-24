# Roadmap

Loom started as a tab manager. The vision is to make it a full **agent orchestrator** — one that manages agent lifecycles, coordinates multi-agent workflows, integrates with external tools, and turns iTerm2's Toolbelt into a mission control panel.

This roadmap is organized into phases. Earlier phases lay the foundation that later phases build on. Items within each phase are roughly ordered by priority.

---

## Phase 1 — Agent Awareness

Loom currently manages terminals. It doesn't know what the agents inside them are doing. This phase gives Loom eyes and ears.

> **Status**: Core implemented. Claude Code has full integration (hooks, activity tracking, session resume, notifications). Other adapters are templates. See [implementation plan](plans/agent-awareness.md) for details.

### Claude Code Hooks Integration ✅

Loom receives real-time events from Claude Code via HTTP hooks (`POST /events`). The toolbelt shows live activity: thinking, tool calls with details ("Editing server.py", "Running: npm test"), errors, and permission prompts. Agent type is auto-detected from the boot command. Hooks are merged into `.claude/settings.local.json` without affecting user config.

Session resume is supported — Loom captures Claude Code's session ID and relaunches with `claude --resume` so conversations survive tab closes and redeployments.

### macOS Notifications ✅

Native macOS notifications (via `osascript`) for agent finished, errors, and attention-needed events. Opt-in per group via group settings. Notifications are batched over a 5-second window to prevent fatigue ("3 agents finished in group 'Backend'").

### Agent Health Monitoring ✅

Background health check runs every 30 seconds. Flags agents with no activity for 5+ minutes or 3+ errors in 5 minutes. `needs_attention` badge clears automatically when the agent resumes work.

### Provider-Agnostic Adapter Framework ✅

Pluggable adapter system with base class, registry, and auto-detection. Claude Code is fully implemented; Codex and Gemini CLI have template adapters (process matching only, ready for hook integration). New agents require only a single adapter file.

### Session Recording

Capture agent transcripts as structured logs. Every command, tool call, and output is stored. Replay later for debugging, auditing, or understanding how a task was solved. This is the foundation for the audit log in later phases.

### Remaining Work

- Session recording (deferred — EventLog is in-memory only for now)
- Session resume as a per-group setting toggle
- Token cost display in the UI (data is collected but not rendered)
- Gemini CLI and Codex adapter hook integration

---

## Phase 2 — Git Worktree Lifecycle

Agents should work in isolation by default. This phase makes worktrees a first-class concept.

> **Status**: Core implemented. See [implementation plan](plans/git-worktree-lifecycle.md) for details.

### Managed Worktrees ✅

Loom creates a git worktree on a dedicated branch when spawning an agent. Worktrees live in `.loom/worktrees/` (configurable), branches named `loom/{agent-name}-{short-id}`. `.loom/` is auto-added to `.gitignore`. Worktrees survive agent stops and are reused on relaunch. Validated on daemon restart. UI shows branch badge with diff stats on agent cells. Manual create/remove via context menu.

### Checkpoints & Rollback ✅

Auto-checkpoint on agent stop (opt-in per group). Manual checkpoint via context menu. Checkpoint commit messages include Claude Code's last assistant message as the body. History modal shows all commits with +/- stats, expandable commit bodies, and per-commit rollback.

### Merge to Main ✅

"Merge to Main" in the worktree submenu sends a customizable prompt to the Claude Code session. Claude performs the merge and resolves conflicts. Loom verifies via `git merge-base --is-ancestor` when the agent finishes — green toast on success, amber attention badge on failure. Merge prompt is configurable per group.

### Remaining Work

- **Auto-PR lifecycle** — abstract PR provider interface + GitHub implementation. "Create PR" context menu, auto-PR on stop, PR URL tracking.
- **Diff viewer modal** — read-only diff view in the toolbelt.
- **Multi-repo support** — groundwork laid with `worktree_repo_root` field, full implementation deferred.

---

## Phase 3 — CLI & Remote Control

Loom shouldn't only be usable from the toolbelt. This phase adds programmatic access.

### `loom` CLI

A terminal command that talks to the Loom daemon over its local socket. Commands include:

- `loom agent new <name>` — create an agent
- `loom agent send <id> <message>` — send input to a running agent
- `loom terminal new` — create a terminal
- `loom broadcast <command>` — broadcast to all agents in a group
- `loom task <description>` — create an agent with a task and worktree in one shot
- `loom status` — show all agents, their state, and current task
- `loom diff <id>` — show what an agent has changed

This makes Loom scriptable — it can be called from shell scripts, CI, other agents, or automation tools.

### Remote Server Mode

Expose Loom over the network (local or internet) as an HTTP/WebSocket API. Same capabilities as the CLI, but accessible from anywhere. This turns a single developer's iTerm2 into a shared agent execution environment.

- API key authentication
- TLS support
- Rate limiting

### MCP Server Bridge

Expose Loom as an MCP (Model Context Protocol) server. Any MCP-aware agent or tool can discover Loom's capabilities — spawn agents, check status, get results — as standard tool calls. This means agents outside Loom can use Loom as infrastructure.

---

## Phase 4 — Workflow Automation

With awareness, worktrees, and programmatic access in place, Loom can start running work autonomously.

### Agent Templates & Playbooks

Predefined agent configurations: "Bug Fix Agent" gets a worktree + issue context + test runner. "Review Agent" gets the diff + repo conventions + a checklist. Templates are shareable YAML/JSON files.

- System prompt, model, tools, and environment per template
- Parameters: fill in the blanks when instantiating ("which issue?", "which branch?")
- Community-contributed template library

### Pipeline Composition

Chain agents into multi-step workflows: Agent 1 implements → Agent 2 writes tests → Agent 3 reviews → auto-create PR if all pass.

- Define pipelines declaratively (YAML/JSON)
- DAG execution: parallel and sequential steps with dependencies
- Conditional branches: "if tests fail, go back to implementation"
- Visual pipeline view in the toolbelt

### Event-Driven Triggers

Start agents or pipelines based on events rather than manual invocation:

- CI failed → spawn a fix agent
- New issue labeled `loom` → pick it up automatically
- PR opened → spawn a review agent
- Cron schedule → "every night, run a dependency-update agent"
- File changed on a branch → re-run validation
- Webhook received → configurable handler

### Retry & Fallback Policies

If an agent fails, Loom can retry with a different prompt strategy, a different model, or escalate to a human. Configurable per template or per pipeline step.

---

## Phase 5 — Task & Ticketing Integration

Agents need to know what to work on. This phase connects Loom to where work is tracked.

### Built-in Task Board

A lightweight Kanban view inside the toolbelt. Tasks move across columns: **Backlog → Assigned → In Progress → Review → Done**. Each card maps to an agent + worktree.

- Create tasks manually or from the CLI
- Assign to an agent template
- Track progress via hooks
- View diffs and approve from the card

### Provider Integrations

Sync the task board with external systems. Each provider is a plugin:

- **Linear** — sync issues bidirectionally, update status, post agent results as comments
- **Jira** — same as Linear
- **GitHub Issues** — same, with tighter PR linkage
- **Notion** — for teams using Notion as a task tracker

The plugin interface is generic: fetch tasks, update status, post comments. New providers are a single adapter file.

### Auto-Assignment

Loom watches the task board (or external provider) and automatically assigns incoming tasks to agents based on templates, priority, and available capacity. The user approves the assignment or lets it run.

---

## Phase 6 — Multi-Agent Coordination

Individual agents are useful. Coordinated agents are powerful.

### Agent-to-Agent Delegation

A lead agent breaks a large task into subtasks and spawns worker agents through Loom. The lead monitors progress, collects results, and synthesizes the final output. Loom visualizes the delegation tree.

### Shared Context Bus

Agents working on the same project can publish findings, decisions, and warnings to a shared context. When Agent B starts, it can read what Agent A discovered without re-exploring the codebase.

- Pub/sub model: agents subscribe to topics
- Persistent within a session or task group
- Accessible via hooks or the CLI

### Conflict Detection

If two agents are editing overlapping files, Loom warns early rather than letting them discover merge conflicts later. Options:

- File-level advisory locks: "Agent 2 is editing `server.py`, proceed with caution"
- Hard locks: prevent concurrent edits to the same file
- Automatic rebase: Loom rebases Agent B's worktree onto Agent A's changes periodically

### Agent Handoff

Structured handoff between agents. Agent A finishes phase 1 (implementation), then hands off to Agent B (review) with full context: what was done, what decisions were made, what's left. The handoff document is auto-generated from session recordings and hook data.

---

## Phase 7 — Multi-Agent Support

Loom shouldn't be locked to Claude Code. This phase makes the agent layer pluggable.

### Provider-Agnostic Agent Interface

Abstract the agent lifecycle behind a protocol:

- `start(prompt, config)` — launch the agent process
- `send(message)` — send input
- `get_status()` — poll state
- `get_output()` — retrieve results
- `stop()` — terminate

Ship adapters for:

- **Claude Code** (primary, richest integration via hooks)
- **Codex** (OpenAI CLI agent)
- **OpenCode**
- **Aider**
- **Goose**
- Custom agents (any CLI tool that reads stdin and writes stdout)

### Per-Agent Configuration

Each agent adapter exposes its own configuration surface: model selection, tool permissions, system prompts, API keys. Loom's template system supports adapter-specific fields.

### Capability Discovery

Not all agents support the same features. Loom discovers what each adapter can do (hooks? status polling? mid-task messaging?) and degrades gracefully. Claude Code gets the full experience; a basic CLI wrapper gets spawn/stop/output.

---

## Phase 8 — Observability & Cost Management

At scale, you need to know what's happening and what it costs.

### Token Metering & Budget Caps

Track API token usage per agent, per task, per day. Set hard limits ("stop after $5") or soft warnings. Show burn rate on each agent cell. Aggregated cost dashboard in the toolbelt.

### Observability Dashboard

Real-time view across all agents:

- What each agent is doing right now
- Timeline of events per agent
- Errors and warnings
- Resource usage (tokens, time, API calls)
- Filterable by group, template, status

### Audit Log

Immutable log of every action Loom and its agents took: commands run, files modified, PRs created, approvals given, costs incurred. Searchable and exportable.

---

## Phase 9 — Security & Access Control

Required for remote mode and team use.

### Auth & RBAC

Role-based access control for the remote API:

- **Viewer** — see agent status and logs
- **Operator** — start/stop agents, approve PRs
- **Admin** — configure templates, manage integrations, set budgets

Supports API keys and OAuth.

### Secrets Management

Agents need tokens (GitHub, Linear, API keys). Loom manages these centrally and injects them into agent environments. No scattered `.env` files.

### Sandboxed Execution

Optionally isolate agent worktrees and terminals:

- Network restrictions (no internet access, or allowlisted domains only)
- File system boundaries (agent can only see its worktree)
- Resource limits (CPU, memory, time)

Important when agents run untrusted code or when operating in a shared environment.

---

## Phase 10 — Toolbelt UX

The toolbelt is Loom's primary interface. It should scale from 1 agent to 50.

### Command Palette

`Cmd+K` style fuzzy finder in the toolbelt. Type to search: "start bug fix", "show agent 3 diff", "broadcast git pull", "open Linear settings". Fast access to everything without navigating menus.

### Minimap & Timeline

Compressed view of all agent activity over time. See at a glance what's running, what finished, what's waiting. Zoom in on any agent to see its event stream.

### Agent Chat

Send a follow-up message to a running agent directly from the toolbelt without switching to its terminal tab. Quick corrections and guidance without context switching.

### Diff Viewer

Inline diff viewer in the toolbelt. See what an agent has changed, approve or reject hunks, leave comments. Lightweight code review without leaving iTerm2.

---

## Cross-Cutting Concerns

These apply across all phases:

- **Backward compatibility** — each phase should be independently useful. Users who only want tab management shouldn't be forced into worktree workflows.
- **Configuration over convention** — features are opt-in. Loom works out of the box with zero config, and each capability is enabled as needed.
- **Performance** — the toolbelt runs in a WKWebView. The UI must stay snappy even with dozens of agents. Heavy work (git operations, API calls, recording) happens in the Python daemon.
- **Dogfooding** — Loom should be used to build Loom. Each phase should be tested by using Loom to coordinate the agents implementing the next phase.
