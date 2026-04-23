"""Weaver MCP tool schema definitions."""


WEAVER_TOOLS = [
    # -- Read tools ---------------------------------------------------------
    {
        "name": "weaver_board_summary",
        "description": (
            "Return a compact board overview for the weaver's group. "
            "Includes lane counts, active agent status, pending asks, "
            "task-health rollups, current non-blocking Weaver hints, and "
            "key label counts without embedding full task lists. Also "
            "includes compact computed stream summaries derived from "
            "branch/worktree state. When owned-agent restriction is "
            "enabled, hint and agent rollups only include agents this "
            "Weaver can control."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_session_map",
        "description": (
            "Return a deterministic structured Session Map for the "
            "weaver's group. This is the current orchestration snapshot "
            "used for recovery: active streams, pending asks and human "
            "gates, unhealthy tasks, verification gates, branch-boundary "
            "state, active agents, queued follow-up work, recent "
            "decision/plan/checkpoint journal entries, and current "
            "deterministic hints. Prefer this when you need synthesis "
            "without rereading the full journal tail."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_streams_list",
        "description": (
            "List computed branch/worktree streams for the weaver's group. "
            "Returns compact stream objects including identity, ownership, "
            "product/workflow membership, product-queue state, queue gates, "
            "recent visibility items, state, live/dormant/orphaned "
            "presence classification, review/boundary metadata, and "
            "recommended next action. Orphaned historical streams are "
            "suppressed by default; pass include_orphaned=true to include "
            "them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": (
                        "Optional stream state filter such as "
                        "'implementing', 'reviewing', "
                        "'awaiting_human_validation', or 'ready_to_merge'."
                    ),
                },
                "branch": {
                    "type": "string",
                    "description": (
                        "Optional branch filter to narrow the returned "
                        "streams."
                    ),
                },
                "repo_root": {
                    "type": "string",
                    "description": (
                        "Optional repo root filter used with branch when "
                        "multiple repos may share the same branch name."
                    ),
                },
                "include_orphaned": {
                    "type": "boolean",
                    "description": (
                        "When true, include orphaned historical streams "
                        "that are hidden from the default Open Streams "
                        "operational view."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_stream_show",
        "description": (
            "Show one computed stream by stream id, branch identity, or a "
            "related task id. Returns the full compact stream payload with "
            "product, workflow, and visibility distinctions preserved, "
            "including queue items, queue gate, and auto-resume readiness."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stream": {
                    "type": "string",
                    "description": (
                        "Stream identifier, stream id, or repo_root::branch."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": (
                        "Related task id or alias to resolve the stream from."
                    ),
                },
                "branch": {
                    "type": "string",
                    "description": (
                        "Branch name when resolving a stream directly."
                    ),
                },
                "repo_root": {
                    "type": "string",
                    "description": (
                        "Repo root used with branch when stream ids are not "
                        "provided."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_board_list",
        "description": (
            "List all tasks on the board grouped by lane. "
            "Supports optional filters by lane, label, task health, or "
            "text search. Returns a summary of each task including "
            "title, slug, lane, labels, action, assigned agent, and "
            "health and linked external ticket metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lane": {
                    "type": "string",
                    "description": "Filter to a specific lane.",
                },
                "label": {
                    "type": "string",
                    "description": "Filter to tasks with this label.",
                },
                "health": {
                    "type": "string",
                    "description": (
                        "Filter to a health state such as blocked, "
                        "idle-risk, stalled, or thrashing."
                    ),
                },
                "search": {
                    "type": "string",
                    "description": (
                        "Text search across task title and description."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_task_show",
        "description": (
            "Show full details for a task by ID or legacy alias. "
            "Returns title, description, labels, action, action variables, "
            "pipeline info, verification metadata, assigned agent, "
            "linked external ticket metadata, attachments/artifacts, "
            "and activity messages. "
            "For pipeline tasks, automatically includes the chain summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_agents_list",
        "description": (
            "List all active agents with their name, slug, status, "
            "group, current task, and activity detail. When owned-agent "
            "restriction is enabled, only agents created by this Weaver "
            "are listed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "weaver_agent_show",
        "description": (
            "Show detailed information about a specific agent. "
            "Returns agent metadata, worktree state (path, branch, "
            "diff stats, checkpoints), task history with messages, "
            "session info, and child terminals. Use for post-completion "
            "review before merging. When owned-agent restriction is "
            "enabled, the target agent must have been created by this Weaver."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent slug, name, or ID.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_actions_list",
        "description": (
            "List available actions (project and user scope) with "
            "name, description, variables, and scope. Use this to "
            "see what actions can be attached to tasks for dispatch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped actions."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_action_show",
        "description": (
            "Show full details of an action including its YAML contents, "
            "prompt template, transitions, and discovered variables."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Action name (e.g. 'feature/implement')."
                    ),
                },
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped actions."
                    ),
                },
            },
            "required": ["name"],
        },
    },
    # -- Write tools --------------------------------------------------------
    {
        "name": "weaver_task_create",
        "description": (
            "Create a new task on the board. Specify a title and "
            "optionally attach an action, group, lane, labels, and "
            "verification metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Longer description or context.",
                },
                "group": {
                    "type": "string",
                    "description": "Target group for the task.",
                },
                "lane": {
                    "type": "string",
                    "description": (
                        "Lane to place the task in (default: Backlog)."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Action name to attach (e.g. 'feature/implement')."
                    ),
                },
                "action_vars": {
                    "type": "object",
                    "description":
                        "Action variable values as key-value pairs.",
                    "additionalProperties": {"type": "string"},
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to attach to the task.",
                },
                "verification_mode": {
                    "type": "string",
                    "enum": ["", "deploy", "restart"],
                    "description": "Optional verification mode summary.",
                },
                "verification_state": {
                    "type": "string",
                    "enum": ["", "pending", "attempted", "passed", "failed"],
                    "description": "Optional verification state summary.",
                },
                "verification_notes": {
                    "type": "string",
                    "description": "Optional verification notes.",
                },
                "verification_summary": {
                    "type": "object",
                    "description": "Optional structured verification summary.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "weaver_task_edit",
        "description": (
            "Edit fields on an existing task. Only the fields you "
            "provide will be updated — omitted fields are unchanged."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias to edit.",
                },
                "title": {
                    "type": "string",
                    "description": "New task title.",
                },
                "description": {
                    "type": "string",
                    "description": "New description.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New label list (replaces existing).",
                },
                "action": {
                    "type": "string",
                    "description": "New action name.",
                },
                "action_vars": {
                    "type": "object",
                    "description": "New action variable values.",
                    "additionalProperties": {"type": "string"},
                },
                "verification_mode": {
                    "type": "string",
                    "enum": ["", "deploy", "restart"],
                    "description": "New verification mode summary.",
                },
                "verification_state": {
                    "type": "string",
                    "enum": ["", "pending", "attempted", "passed", "failed"],
                    "description": "New verification state summary.",
                },
                "verification_notes": {
                    "type": "string",
                    "description": "New verification notes.",
                },
                "verification_summary": {
                    "type": "object",
                    "description": "New structured verification summary.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_upload_artifact",
        "description": (
            "Upload and attach an image or other artifact to a specific board "
            "task. Provide a local_path or inline content, and Loom stores the "
            "file on the task and returns normalized artifact metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias to attach the artifact to.",
                },
                "local_path": {
                    "type": "string",
                    "description": "Local filesystem path to upload from.",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Optional filename override. Required for inline uploads."
                    ),
                },
                "content_base64": {
                    "type": "string",
                    "description": (
                        "Base64-encoded file content for binary inline uploads."
                    ),
                },
                "content_text": {
                    "type": "string",
                    "description": (
                        "Plain-text inline content to write as a task artifact."
                    ),
                },
                "artifact_type": {
                    "type": "string",
                    "description": (
                        "Optional artifact type override such as image, diff, log, "
                        "test_report, generated_doc, or file_ref."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Optional display title for the artifact.",
                },
                "mime_type": {
                    "type": "string",
                    "description": "Optional MIME type override.",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional human-readable summary.",
                },
                "prompt_mode": {
                    "type": "string",
                    "enum": ["auto", "none", "path", "summary", "inline"],
                    "description": "Optional prompt shaping mode.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_verify",
        "description": (
            "Record a deploy/restart verification checkpoint for a task. "
            "Use this for explicit attempt, smoke pass/fail, and notes "
            "without routing through a generic task edit."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias to update.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["deploy", "restart"],
                    "description": "Whether the checkpoint is for deploy or restart.",
                },
                "state": {
                    "type": "string",
                    "enum": ["pending", "attempted", "passed", "failed", ""],
                    "description": "Optional explicit verification state override.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form verification notes.",
                },
                "tests_run": {
                    "type": "string",
                    "description": "Short summary of tests that were run.",
                },
                "human_validation_pending": {
                    "type": "string",
                    "description": "What still needs human validation.",
                },
                "deploy_needed": {
                    "type": "boolean",
                    "description": "Whether a deploy is still required.",
                },
                "attempted": {
                    "type": "boolean",
                    "description": "Set or clear the recorded deploy/restart attempted flag.",
                },
                "smoke": {
                    "type": "string",
                    "enum": ["passed", "failed", "clear"],
                    "description": "Record a smoke result, or clear smoke completion with `clear`.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_task_move",
        "description": "Move a task to a different lane on the board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias.",
                },
                "lane": {
                    "type": "string",
                    "description": "Target lane name.",
                },
            },
            "required": ["task", "lane"],
        },
    },
    {
        "name": "weaver_task_dispatch",
        "description": (
            "Dispatch a task to an agent. Creates a new agent by "
            "default, or dispatches to an existing agent if specified. "
            "The task moves to In Progress and the agent receives "
            "the rendered prompt. When owned-agent restriction is enabled, "
            "existing-agent dispatch can only target agents created by this Weaver."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or legacy alias to dispatch.",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Existing agent ID or name to dispatch to. "
                        "If omitted, a new agent is created."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Name for the new agent (e.g. 'worker'). "
                        "Only used when creating a new agent."
                    ),
                },
                "agent_type": {
                    "type": "string",
                    "description": (
                        "Agent backend for a new agent "
                        "(e.g. 'claude-code', 'codex')."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Boot command override for a new agent. "
                        "Only used when creating a new agent."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Model override for a new agent. Only used when "
                        "creating a new agent and when the boot command "
                        "is not fully overridden."
                    ),
                },
                "reasoning_effort": {
                    "type": "string",
                    "description": (
                        "Reasoning-effort override for a new agent. Only "
                        "used when creating a new agent and when the boot "
                        "command is not fully overridden."
                    ),
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "weaver_batch_dispatch",
        "description": (
            "Dispatch a planned batch of tasks with an explicit "
            "or default concurrency cap. Tasks are processed in request order. "
            "When capacity is full, remaining tasks are queued "
            "persistently and auto-dispatched later as slots open. "
            "Tasks that share an agent_group are routed to the same "
            "agent so later tasks can queue behind earlier ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": (
                        "Ordered task entries. Each item must include "
                        "a task ID or legacy alias and may include an "
                        "agent_group string to keep related tasks on "
                        "the same agent. Deferred entries keep their "
                        "order across restart."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "Task ID or legacy alias.",
                            },
                            "agent_group": {
                                "type": "string",
                                "description": (
                                    "Optional grouping key. Entries "
                                    "with the same value share a "
                                    "single agent within this batch."
                                ),
                            },
                        },
                        "required": ["task"],
                    },
                },
                "max_concurrent": {
                    "type": "integer",
                    "description": (
                        "Maximum number of active worker agents "
                        "allowed in the group after this call. If "
                        "omitted, Loom uses the group's stored Weaver "
                        "default."
                    ),
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "weaver_task_resolve",
        "description": (
            "Resolve an ask task by providing an answer. The answer "
            "is sent to the parent task's agent and the ask task "
            "moves to Done."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Slug or ID of the ask task to resolve."
                    ),
                },
                "answer": {
                    "type": "string",
                    "description": "The answer to send to the agent.",
                },
            },
            "required": ["task", "answer"],
        },
    },
    # -- Event tools --------------------------------------------------------
    {
        "name": "weaver_events",
        "description": (
            "Poll for recent events. Use after context cleanup to "
            "catch up on what happened. Returns panel events filtered "
            "by group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "since_id": {
                    "type": "integer",
                    "description": (
                        "Return events after this event ID (cursor). "
                        "Omit for latest events."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description":
                        "Max events to return (default: 50).",
                },
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter to specific event types. "
                        "Omit for all types."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_launch_settings",
        "description": (
            "Update the designated engineer's persisted launch settings. "
            "These settings are used the next time the designated engineer is created "
            "or relaunched."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider/backend override for the designated "
                        "designated engineer (for example 'codex' or 'claude-code')."
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Boot command override for the designated engineer."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "Model override for the designated engineer.",
                },
                "reasoning_effort": {
                    "type": "string",
                    "description": (
                        "Reasoning-effort override for the designated engineer."
                    ),
                },
            },
        },
    },
    {
        "name": "weaver_notifications",
        "description": (
            "Configure digest noise. Use a quiet, normal, or noisy preset "
            "for fast tuning, or override digest verbosity, event types, "
            "and intervals directly. Mandatory events "
            "(task_completed, agent_error, agent_reply, agent_blocked, "
            "ask_created) are always included."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["quiet", "normal", "noisy"],
                    "description": (
                        "Named notification preset. Quiet favors major "
                        "milestones only, normal matches Loom defaults, "
                        "and noisy adds faster, more detailed updates."
                    ),
                },
                "digest_verbosity": {
                    "type": "string",
                    "enum": ["compact", "balanced", "detailed"],
                    "description": (
                        "Digest detail level. Presets also set this."
                    ),
                },
                "push_interval": {
                    "type": "integer",
                    "description": (
                        "Seconds between digest pushes "
                        "(min: 10, default: 60)."
                    ),
                },
                "max_interval": {
                    "type": "integer",
                    "description": (
                        "Max seconds between regular digests "
                        "(default: 300)."
                    ),
                },
                "heartbeat_interval": {
                    "type": "integer",
                    "description": (
                        "Send an idle heartbeat if no digest was sent "
                        "for this many seconds (0 = off, default: 300)."
                    ),
                },
                "enable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional event types to enable: "
                        "agent_started, task_dispatched, "
                        "task_derived, agent_progress."
                    ),
                },
                "disable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional event types to disable.",
                },
            },
        },
    },
    {
        "name": "weaver_resume",
        "description": (
            "Resume event delivery after a weaver_ask. Call this "
            "after the human has responded (via the panel or "
            "directly in your terminal) to unpause event pushes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # -- Context tools ------------------------------------------------------
    {
        "name": "weaver_journal",
        "description": (
            "Append an entry to the weaver's persistent decision "
            "journal. Use this to record decisions, observations, and "
            "periodic checkpoints. The journal survives context "
            "cleanup — read it back with weaver_journal_read to "
            "resume orchestration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "decision", "observation",
                        "checkpoint", "plan",
                    ],
                    "description": (
                        "Entry type: decision (action taken + "
                        "rationale), observation (something noted), "
                        "checkpoint (board state summary for context "
                        "recovery), plan (intended next steps)."
                    ),
                },
                "entry": {
                    "type": "string",
                    "description": (
                        "Journal entry content. Be concise but "
                        "include rationale for decisions."
                    ),
                },
            },
            "required": ["type", "entry"],
        },
    },
    {
        "name": "weaver_journal_read",
        "description": (
            "Read recent journal entries. Use after context cleanup "
            "or startup to recover the weaver's decision history "
            "and resume orchestration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tail": {
                    "type": "integer",
                    "description": (
                        "Number of most recent entries to return "
                        "(default: 20)."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": [
                        "decision", "observation",
                        "checkpoint", "plan",
                    ],
                    "description":
                        "Filter to a specific entry type.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["self", "group"],
                    "description": (
                        "For engineer_journal_read, defaults to self "
                        "(only entries authored by this engineer). Pass "
                        "group to intentionally read cross-author group "
                        "journal entries."
                    ),
                },
                "include_cross_author": {
                    "type": "boolean",
                    "description": (
                        "For engineer_journal_read, true is equivalent "
                        "to scope=group. Defaults to false."
                    ),
                },
            },
        },
    },
    # -- Interaction tools --------------------------------------------------
    {
        "name": "weaver_agent_message",
        "description": (
            "Send a message to any agent's terminal. The agent can "
            "reply via loom_reply, which appears in the weaver's "
            "next event digest. Loom also creates a visible follow-up "
            "task for the exchange and returns its task id. Use for: "
            "redirecting agents, providing context, answering questions. "
            "When owned-agent restriction is enabled, only agents "
            "created by this Weaver can be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name.",
                },
                "message": {
                    "type": "string",
                    "description":
                        "Message to send to the agent.",
                },
            },
            "required": ["agent", "message"],
        },
    },
    {
        "name": "weaver_ask",
        "description": (
            "Ask the human a question. The question is displayed in "
            "the Agent panel and event pushes are automatically "
            "paused until the human replies. Use this only when the "
            "next orchestration step depends on a blocking human "
            "decision — prioritization, design decisions, approval, "
            "or clarification that should stop dispatch until "
            "answered. Do not use this for status updates, soft "
            "questions, or next-wave proposals; use weaver_note for "
            "those. After the human responds (via the panel or "
            "directly in your terminal), call weaver_resume to "
            "unpause event delivery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question for the human.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "weaver_note",
        "description": (
            "Post a non-blocking note or soft question for the human, "
            "including next-wave proposals or status/context that "
            "should stay visible without pausing orchestration. "
            "Unlike weaver_ask, this does not pause event delivery or "
            "put Loom into awaiting-input mode."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The note or soft question for the human.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["note", "question"],
                    "description": "Render as an informational note or a soft question.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "weaver_agent_close",
        "description": (
            "Close an agent — ends its terminal session and removes "
            "it from the group. The agent's worktree (if any) is "
            "preserved on disk. Use after merging or when the agent "
            "is no longer needed. When owned-agent restriction is "
            "enabled, only agents created by this Weaver can be closed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name to close.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_agent_relaunch",
        "description": (
            "Relaunch a stopped agent — re-creates the terminal "
            "session. If the agent has a worktree, it is reused. "
            "If session_resume is enabled, the previous Claude Code "
            "session is resumed. When owned-agent restriction is "
            "enabled, only agents created by this Weaver can be relaunched."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name to relaunch.",
                },
            },
            "required": ["agent"],
        },
    },
    # -- Worktree tools -----------------------------------------------------
    {
        "name": "weaver_merge",
        "description": (
            "Merge an agent's worktree branch into the base branch "
            "(usually main). Uses server-side merge — no interactive "
            "resolution. If there are conflicts, use weaver_rebase "
            "to replay the branch onto base before retrying the merge. "
            "When owned-agent restriction is enabled, only agents created "
            "by this Weaver can be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Custom merge commit message. If omitted, "
                        "auto-generated from completed tasks."
                    ),
                },
                "close_agent_on_merge": {
                    "type": "boolean",
                    "description": (
                        "Close the agent after a successful merge."
                    ),
                },
                "remove_worktree_on_merge": {
                    "type": "boolean",
                    "description": (
                        "Remove the worktree after a successful merge."
                    ),
                },
                "auto_move_to_done": {
                    "type": "boolean",
                    "description": (
                        "When merge cleanup closes or removes the worker "
                        "context, also move the sole linked active board "
                        "task to Done. Defaults to true."
                    ),
                },
                "force_stale_base": {
                    "type": "boolean",
                    "description": (
                        "Bypass the stale-base merge safety gate. Use only "
                        "after intentionally accepting that the branch forked "
                        "before the current base and re-running review is not "
                        "desired."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Bypass the sibling-branch divergence safety gate. "
                        "Use only after diffing sibling review/implement "
                        "branches and intentionally accepting any commits "
                        "not present on the merge target."
                    ),
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_rebase",
        "description": (
            "Rebase an agent's worktree branch onto its base branch. "
            "Useful after weaver_merge reports conflicts. Returns "
            "post-rebase merge readiness, and aborts automatically "
            "if conflicts still require manual resolution. When owned-agent "
            "restriction is enabled, only agents created by this Weaver can "
            "be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_create_pr",
        "description": (
            "Create a GitHub pull request for an agent's worktree "
            "branch. Pushes the branch to origin and creates a PR "
            "via the GitHub CLI (gh). Returns the PR URL. When owned-agent "
            "restriction is enabled, only agents created by this Weaver can "
            "be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "PR title. If omitted, uses the agent's "
                        "linked task title or agent name."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "PR description body (markdown).",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_diff",
        "description": (
            "Get the diff of an agent's worktree branch against "
            "its base branch. Can return a structured summary, "
            "diffstat, or full diff output, optionally limited to "
            "specific files. Useful for reviewing changes before "
            "merge or PR. Reviewer workers may share the implementer's "
            "worktree, so targeting a reviewer reports that shared branch. "
            "When owned-agent restriction is enabled, only agents created "
            "by this Weaver can be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
                "stat_only": {
                    "type": "boolean",
                    "description": (
                        "If true, return only the diffstat summary "
                        "(files changed, insertions, deletions) "
                        "instead of the full diff. Default: false."
                    ),
                },
                "summary_only": {
                    "type": "boolean",
                    "description": (
                        "If true, return a machine-readable diff "
                        "summary with changed files and lightweight "
                        "review signals instead of raw diff text. "
                        "Default: false."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Limit diff to specific file paths. "
                        "If omitted, shows all changes."
                    ),
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_worktree_remove",
        "description": (
            "Remove an agent's worktree from disk. Use after merging "
            "to clean up. The agent's directory reverts to the "
            "original repo root. When owned-agent restriction is enabled, "
            "only agents created by this Weaver can be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "weaver_worktree_checkpoint",
        "description": (
            "Create a checkpoint commit on an agent's worktree. "
            "Commits all current changes with an auto-generated "
            "message. Useful for saving progress before risky "
            "operations. When targeting a reviewer that shares an "
            "implementer's worktree, this snapshots the shared branch. "
            "When owned-agent restriction is enabled, only agents "
            "created by this Weaver can be targeted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree.",
                },
            },
            "required": ["agent"],
        },
    },
]
