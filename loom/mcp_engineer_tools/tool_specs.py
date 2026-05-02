"""Engineer MCP tool schema definitions."""


ENGINEER_TOOLS = [
    # -- Read tools ---------------------------------------------------------
    {
        "name": "engineer_board_summary",
        "description": (
            "Return a compact board overview for the engineer's group. "
            "Includes lane counts, active agent status, pending asks, "
            "task-health rollups, current non-blocking Engineer hints, and "
            "key label counts without embedding full task lists. Also "
            "includes compact computed stream summaries derived from "
            "branch/worktree state. When owned-agent restriction is "
            "enabled, hint and agent rollups only include agents this "
            "Engineer can control."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "engineer_session_map",
        "description": (
            "Return a deterministic structured Session Map for the "
            "engineer's group. This is the current orchestration snapshot "
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
        "name": "engineer_streams_list",
        "description": (
            "List computed branch/worktree streams for the engineer's group. "
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
        "name": "engineer_stream_show",
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
        "name": "engineer_board_list",
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
        "name": "engineer_task_show",
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
        "name": "engineer_agents_list",
        "description": (
            "List all active agents with their name, slug, status, "
            "group, current task, and activity detail. When owned-agent "
            "restriction is enabled, only agents created by this Engineer "
            "are listed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "engineer_agent_show",
        "description": (
            "Show detailed information about a specific agent. "
            "Returns agent metadata, worktree state (path, branch, "
            "diff stats, checkpoints), task history with messages, "
            "session info, and child terminals. Use for post-completion "
            "review before merging. When owned-agent restriction is "
            "enabled, the target agent must have been created by this Engineer."
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
        "name": "engineer_actions_list",
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
        "name": "engineer_action_show",
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
        "name": "engineer_task_create",
        "description": (
            "Create a board task. Required: title. Optional: group, lane, "
            "action/action_vars, labels, verification fields, deliverable."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Task context.",
                },
                "group": {
                    "type": "string",
                    "description": "Task group.",
                },
                "lane": {
                    "type": "string",
                    "description": "Destination lane (default: Backlog).",
                },
                "action": {
                    "type": "string",
                    "description": (
                        "Action name to attach (e.g. 'feature/implement')."
                    ),
                },
                "action_vars": {
                    "type": "object",
                    "description": "Action variable values.",
                    "additionalProperties": {"type": "string"},
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task labels.",
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
                "deliverable": {
                    "type": "object",
                    "description": (
                        "Deliverable contract. If `required: true`, worker "
                        "must upload a matching artifact with "
                        "`loom_task_upload_artifact` before "
                        "`loom_done`/`loom_ready`; overrides action template."
                    ),
                    "properties": {
                        "required": {"type": "boolean"},
                        "type": {"type": "string"},
                        "format": {"type": "string"},
                        "artifact_title": {"type": "string"},
                    },
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "engineer_task_edit",
        "description": (
            "Patch task fields; omitted fields stay unchanged."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or alias.",
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
        "name": "engineer_task_upload_artifact",
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
        "name": "engineer_task_verify",
        "description": (
            "Record a task verification checkpoint for deploy/restart "
            "attempts, smoke results, and notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or alias.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["deploy", "restart"],
                    "description": "Verification mode.",
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
                    "description": "Tests run.",
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
                    "description": "Record smoke result, or clear completion.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "engineer_task_move",
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
        "name": "engineer_task_dispatch",
        "description": (
            "Dispatch task; create an agent unless `agent` is set. Moves "
            "task to In Progress and sends rendered prompt. Existing-agent "
            "targets must be owned when ownership restriction is on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task ID or alias.",
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
        "name": "engineer_batch_dispatch",
        "description": (
            "Dispatch tasks in order with a concurrency cap; excess entries "
            "persistently queue and auto-dispatch as slots open. Same "
            "`agent_group` values share one agent."
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
                                "description": "Task ID or alias.",
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
                        "omitted, Loom uses the group's stored Engineer "
                        "default."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider/backend override for new worker agents "
                        "created by this batch (for example 'codex' or "
                        "'claude-code'). If omitted, Loom uses the group's "
                        "default worker provider."
                    ),
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "engineer_task_resolve",
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
        "name": "engineer_events",
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
        "name": "engineer_launch_settings",
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
        "name": "engineer_notifications",
        "description": (
            "Set digest preset or overrides. Mandatory events "
            "(task_completed, agent_error, agent_reply, agent_blocked, "
            "ask_created) stay enabled."
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
        "name": "engineer_resume",
        "description": (
            "Resume event delivery after a engineer_ask. Call this "
            "after the human has responded (via the panel or "
            "directly in your terminal) to unpause event pushes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    # -- Context tools ------------------------------------------------------
    {
        "name": "engineer_journal",
        "description": (
            "Append an entry to the engineer's persistent decision "
            "journal. Use this to record decisions, observations, and "
            "periodic checkpoints. The journal survives context "
            "cleanup — read it back with engineer_journal_read to "
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
                        "note_dismissed", "qa",
                    ],
                    "description": (
                        "Entry type: decision (action taken + "
                        "rationale), observation (something noted), "
                        "checkpoint (board state summary for context "
                        "recovery), plan (intended next steps), "
                        "note_dismissed (preserved dismissed note), "
                        "qa (human question/answer exchange)."
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
        "name": "engineer_journal_read",
        "description": (
            "Read recent journal entries. Use after context cleanup "
            "or startup to recover the engineer's decision history "
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
                        "note_dismissed", "qa",
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
        "name": "engineer_agent_message",
        "description": (
            "Send a message to any agent's terminal. The agent can "
            "reply via loom_reply when reply_required is true, which "
            "appears in the engineer's next event digest. By default "
            "Loom creates a visible follow-up task for the exchange and "
            "returns its task id. Set reply_required=false only for "
            "routine context/redirect messages that do not need an "
            "answer; Loom persists those on the worker's current task "
            "inline thread instead of creating a board task. Use for: "
            "redirecting agents, providing context, answering questions. "
            "When owned-agent restriction is enabled, only agents "
            "created by this Engineer can be targeted."
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
                "reply_required": {
                    "type": "boolean",
                    "description": (
                        "Whether the agent must reply via loom_reply. "
                        "Defaults true for backward compatibility; set "
                        "false for informational redirects/context that "
                        "should be stored inline on the parent task."
                    ),
                    "default": True,
                },
            },
            "required": ["agent", "message"],
        },
    },
    {
        "name": "engineer_ask",
        "description": (
            "Ask the human a question. The question is displayed in "
            "the Agent panel and event pushes are automatically "
            "paused until the human replies. Use this only when the "
            "next orchestration step depends on a blocking human "
            "decision — prioritization, design decisions, approval, "
            "or clarification that should stop dispatch until "
            "answered. Do not use this for status updates, soft "
            "questions, or next-wave proposals; use engineer_note for "
            "those. After the human responds (via the panel or "
            "directly in your terminal), call engineer_resume to "
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
        "name": "engineer_note",
        "description": (
            "Post a non-blocking note or soft question for the human, "
            "including next-wave proposals or status/context that "
            "should stay visible without pausing orchestration. "
            "Unlike engineer_ask, this does not pause event delivery or "
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
        "name": "engineer_agent_close",
        "description": (
            "Close an agent — ends its terminal session and removes "
            "it from the group. The agent's worktree (if any) is "
            "preserved on disk. Use after merging or when the agent "
            "is no longer needed. When owned-agent restriction is "
            "enabled, only agents created by this Engineer can be closed."
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
        "name": "engineer_agent_relaunch",
        "description": (
            "Relaunch a stopped agent — re-creates the terminal "
            "session. If the agent has a worktree, it is reused. "
            "If session_resume is enabled, the previous Claude Code "
            "session is resumed. When owned-agent restriction is "
            "enabled, only agents created by this Engineer can be relaunched."
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
        "name": "engineer_merge",
        "description": (
            "Merge agent worktree branch into base. Non-interactive; on "
            "conflicts, run engineer_rebase then retry. Targets must be "
            "owned when ownership restriction is on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID/name with worktree.",
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
        "name": "engineer_rebase",
        "description": (
            "Rebase an agent's worktree branch onto its base branch. "
            "Useful after engineer_merge reports conflicts. Returns "
            "post-rebase merge readiness, and aborts automatically "
            "if conflicts still require manual resolution. When owned-agent "
            "restriction is enabled, only agents created by this Engineer can "
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
        "name": "engineer_create_pr",
        "description": (
            "Create a GitHub pull request for an agent's worktree "
            "branch. Pushes the branch to origin and creates a PR "
            "via the GitHub CLI (gh). Returns the PR URL. When owned-agent "
            "restriction is enabled, only agents created by this Engineer can "
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
        "name": "engineer_diff",
        "description": (
            "Return agent worktree diff against base as full diff, diffstat, "
            "or structured summary; optional path filter. Reviewer agents may "
            "share implementer branch. Targets must be owned when restriction "
            "is on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID/name with worktree.",
                },
                "stat_only": {
                    "type": "boolean",
                    "description": "Return only diffstat. Default false.",
                },
                "summary_only": {
                    "type": "boolean",
                    "description": (
                        "Return structured changed-file summary and review "
                        "signals. Default false."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Limit diff to file paths; omit for all.",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "engineer_worktree_remove",
        "description": (
            "Remove an agent's worktree from disk. Use after merging "
            "to clean up. The agent's directory reverts to the "
            "original repo root. When owned-agent restriction is enabled, "
            "only agents created by this Engineer can be targeted."
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
        "name": "engineer_worktree_checkpoint",
        "description": (
            "Create a checkpoint commit on an agent's worktree. "
            "Commits all current changes with an auto-generated "
            "message. Useful for saving progress before risky "
            "operations. When targeting a reviewer that shares an "
            "implementer's worktree, this snapshots the shared branch. "
            "When owned-agent restriction is enabled, only agents "
            "created by this Engineer can be targeted."
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
    # -- Specializations -------------------------------------------------
    {
        "name": "engineer_specializations_list",
        "description": (
            "List engineer specializations available (project and user "
            "scope). Specializations shape an engineer's boot preamble "
            "and triage priorities (e.g. 'ui-ux', 'security-focused')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped "
                        "specializations."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": (
                        "Optional scope filter: 'project' or 'user'. "
                        "Default returns both."
                    ),
                },
            },
        },
    },
    {
        "name": "engineer_specialization_show",
        "description": (
            "Show full details of a specialization including its "
            "description, preamble, and priorities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Specialization name (e.g. 'ui-ux').",
                },
                "group": {
                    "type": "string",
                    "description": (
                        "Group name to resolve project-scoped "
                        "specializations."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": "Optional scope: 'project' or 'user'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "engineer_specialization_save",
        "description": (
            "Create or update a specialization YAML file. Fields: "
            "description, preamble, priorities (list of strings). Saves "
            "to the requested scope (default: project)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Specialization name (file slug).",
                },
                "data": {
                    "type": "object",
                    "description": (
                        "Specialization fields: description, preamble, "
                        "priorities."
                    ),
                },
                "scope": {
                    "type": "string",
                    "description": (
                        "'project' (default) or 'user' (~/.loom/"
                        "specializations)."
                    ),
                },
                "group": {
                    "type": "string",
                    "description": "Group name for project scope resolution.",
                },
            },
            "required": ["name", "data"],
        },
    },
    {
        "name": "engineer_specialization_delete",
        "description": (
            "Delete a specialization YAML file by name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Specialization name to delete.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional scope: 'project' or 'user'.",
                },
                "group": {
                    "type": "string",
                    "description": "Group name for project scope resolution.",
                },
            },
            "required": ["name"],
        },
    },
]
