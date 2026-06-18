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
            "includes compact board_sync state for tasks currently tracked "
            "by an external board provider. "
            "includes compact computed stream summaries derived from "
            "branch/worktree state and recent dispatch-shape metrics. When "
            "owned-agent restriction is enabled, hint and agent rollups only "
            "include agents this Engineer can control."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "engineer_boot_summary",
        "description": (
            "Return this Engineer's cached AI boot-recovery summary. "
            "Read-only: never performs a live provider call. If the status is "
            "empty, stale, refreshing, or error, fall back to "
            "engineer_journal_read while keeping engineer_session_map as the "
            "deterministic snapshot."
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
            "deterministic hints, plus recent dispatch-shape metrics. Prefer "
            "this when you need synthesis without rereading the full journal "
            "tail."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "engineer_hint_snooze",
        "description": (
            "Snooze or clear a deterministic Engineer hint by fingerprint. "
            "Use this to acknowledge expected low-noise hints, such as "
            "merged workers retained by the group's keep-warm cleanup "
            "policy, without changing cleanup policy or deleting anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fingerprint": {
                    "type": "string",
                    "description": (
                        "Exact hint fingerprint from engineer_board_summary "
                        "or engineer_session_map."
                    ),
                },
                "hours": {
                    "type": "number",
                    "description": (
                        "How many hours to snooze. Defaults to 168. Values "
                        "<= 0 clear the existing snooze."
                    ),
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear this fingerprint's snooze.",
                },
            },
            "required": ["fingerprint"],
        },
    },
    {
        "name": "engineer_semantic_recall",
        "description": (
            "Search the local AI semantic index for snippets visible to this "
            "Engineer. Results are over-fetched then filtered through the "
            "same Engineer task, journal, and peer-inspection visibility "
            "rules before any text is returned."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language recall query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum visible snippets to return (default 5, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "engineer_streams_list",
        "description": (
            "List computed branch/worktree streams for the engineer's group. "
            "Returns compact stream objects including identity, ownership, "
            "product/workflow membership, product-queue state, queue gates, "
            "recent visibility items, state, live/dormant/orphaned "
            "presence classification, review/boundary metadata, merge-readiness "
            "packet with report snippet, and recommended next action. Orphaned "
            "historical streams are "
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
                        "'implementing', 'reviewing', 'fixing_blockers', "
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
            "including queue items, queue gate, auto-resume readiness, and "
            "the merge_readiness packet for merge/report decisions."
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
        "name": "engineer_peer_list",
        "description": (
            "List same-group Engineer peers hired by the same Architect. "
            "This discovery is only for the explicit peer notify surface and "
            "does not grant generic access to peer Engineers or their workers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_dismissed": {
                    "type": "boolean",
                    "description": "Include dismissed eligible peer Engineers.",
                },
            },
        },
    },
    {
        "name": "engineer_peer_inbox",
        "description": (
            "Read durable Engineer↔Engineer peer notification threads involving "
            "this Engineer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "peer_engineer_id": {
                    "type": "string",
                    "description": "Optional peer Engineer id/slug/name filter.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Optional thread id filter.",
                },
                "requires_reply": {
                    "type": "boolean",
                    "description": "Only return threads with unanswered incoming ack-required messages.",
                },
                "since": {
                    "type": "number",
                    "description": "Optional unix timestamp lower bound.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum threads to return (default 20, max 100).",
                },
            },
        },
    },
    {
        "name": "engineer_peer_inspect",
        "description": (
            "Inspect the read-only task/stream context granted by one "
            "Engineer↔Engineer peer notification thread. Requires this "
            "Engineer to be a thread participant."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "include_live": {
                    "type": "boolean",
                    "description": "Include revalidated live task context when available.",
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
            "health, linked external ticket metadata, and board_sync state "
            "when present."
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
            "linked external ticket metadata, board_sync state, "
            "attachments/artifacts, "
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
        "name": "engineer_peer_notify",
        "description": (
            "Notify a same-group, same-supervising-Architect Engineer peer to "
            "inspect a referenced task or stream. Requires context_task_ids or "
            "context_stream_refs; context_summary alone is rejected. This does "
            "not create a board task and does not grant generic peer access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "engineer_id": {
                    "type": "string",
                    "description": "Peer Engineer id/slug/name.",
                },
                "message": {"type": "string", "description": "Notification message."},
                "context_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Visible task ids/aliases to grant read-only inspect context for.",
                },
                "context_stream_refs": {
                    "type": "array",
                    "items": {},
                    "description": "Visible stream refs (string or object with stream/branch/repo_root/task).",
                },
                "context_summary": {
                    "type": "string",
                    "description": "Optional concise summary; insufficient without task or stream refs.",
                },
                "ack_required": {
                    "type": "boolean",
                    "description": "Whether the peer should reply/ack.",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Optional existing Engineer peer thread id to continue.",
                },
            },
            "required": ["engineer_id", "message"],
        },
    },
    {
        "name": "engineer_peer_reply",
        "description": "Reply to an existing Engineer↔Engineer peer notification thread.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Existing Engineer peer message id.",
                },
                "message": {"type": "string", "description": "Reply content."},
                "ack_required": {
                    "type": "boolean",
                    "description": "Whether this reply needs a peer answer.",
                },
            },
            "required": ["message_id", "message"],
        },
    },
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
                        "`torque_task_upload_artifact` before "
                        "`torque_done`/`torque_ready`; overrides action template."
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
            "task. Provide a local_path or inline content, and Torque stores the "
            "file on the task and returns normalized artifact metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Task ID or legacy alias to attach the artifact to. "
                        "Prefer this parameter; task_id is accepted as an alias."
                    ),
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "Alias for task, accepted for compatibility with older "
                        "engineer deliverable prompts."
                    ),
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
            "anyOf": [
                {"required": ["task"]},
                {"required": ["task_id"]},
            ],
        },
    },
    {
        "name": "engineer_task_mark_covered",
        "description": (
            "Mark an assigned/created task as covered by another visible "
            "task or PR. Records durable completion evidence and an activity "
            "message; set move_to_done=true to close the covered card."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Covered task ID or alias.",
                },
                "covering_task": {
                    "type": "string",
                    "description": (
                        "Optional visible task ID/alias whose work covers this card."
                    ),
                },
                "covering_task_id": {
                    "type": "string",
                    "description": "Alias for covering_task.",
                },
                "pr_url": {
                    "type": "string",
                    "description": "Optional PR URL that covers the card.",
                },
                "sha": {
                    "type": "string",
                    "description": "Optional commit or merge SHA evidence.",
                },
                "tests_run": {
                    "type": "string",
                    "description": "Optional tests/checks evidence.",
                },
                "evidence": {
                    "type": "string",
                    "description": "Optional concise evidence summary.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional additional notes.",
                },
                "move_to_done": {
                    "type": "boolean",
                    "description": "Move the covered card to Done after recording evidence.",
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
                "test_outcome": {
                    "type": "string",
                    "enum": [
                        "passed",
                        "full_suite_passed",
                        "full_suite_attempted",
                        "unrelated_flake_accepted",
                        "narrower_suite_accepted",
                        "failed",
                    ],
                    "description": "Structured test outcome taxonomy.",
                },
                "full_suite_attempted": {
                    "type": "boolean",
                    "description": "Whether the full test suite was attempted.",
                },
                "unrelated_flake_accepted": {
                    "type": "boolean",
                    "description": "Whether an unrelated flaky failure is accepted with evidence.",
                },
                "isolated_rerun_evidence": {
                    "type": "string",
                    "description": "Focused or isolated rerun evidence supporting flake acceptance.",
                },
                "reviewer_acceptance": {
                    "type": "string",
                    "enum": ["accepted_flake_evidence", "accepted_narrower_suite"],
                    "description": "Reviewer acceptance of flake evidence or narrower-suite coverage.",
                },
                "live_smoke_pending": {
                    "type": "boolean",
                    "description": "Whether live smoke remains pending operator-side.",
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
                        "Deprecated alias for `provider`: agent backend "
                        "for a new agent (e.g. 'claude-code', 'codex')."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider/backend override for a new worker agent "
                        "(for example 'codex' or 'claude-code'). Use this "
                        "with `command` when booting a different provider "
                        "than the group default so Torque selects the "
                        "matching adapter."
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
                "adopt_worktree_path": {
                    "type": "string",
                    "description": "Create the new worker attached to this existing orphaned worktree path instead of creating a branch.",
                },
                "adopt_branch": {
                    "type": "string",
                    "description": "Existing branch to adopt with adopt_worktree_path.",
                },
                "adopt_base_branch": {
                    "type": "string",
                    "description": "Base branch for the adopted worktree.",
                },
                "adopt_repo_root": {
                    "type": "string",
                    "description": "Optional main repo root for the adopted worktree.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "engineer_batch_dispatch",
        "description": (
            "Boot N workers simultaneously for independent tasks. Use when "
            "tasks have no inter-dependencies and parallel velocity > "
            "review-boundary granularity. Independent entries start separate "
            "workers up to `max_concurrent`, this batch's engineer-group "
            "active-worker cap; excess entries persistently queue and "
            "auto-dispatch as slots open. Use shared `agent_group` for a "
            "warm-cluster queue on one agent instead. Example: batch three "
            "disjoint research or test hardening tasks; prefer serial "
            "`engineer_task_dispatch` for implement→review→fix checkpoints, "
            "or a warm cluster for tightly coupled follow-ups that should "
            "share one branch."
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
                        "the same agent. Independent entries without a "
                        "shared agent_group can launch in parallel up to "
                        "max_concurrent. Deferred entries keep their order "
                        "across restart."
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
                                    "Optional warm-cluster same-agent "
                                    "affinity key. Entries with the same "
                                    "value share a single agent within this "
                                    "batch instead of booting independent "
                                    "workers; this is not a capacity or "
                                    "concurrency group."
                                ),
                            },
                        },
                        "required": ["task"],
                    },
                },
                "max_concurrent": {
                    "type": "integer",
                    "description": (
                        "Per-batch active-worker cap, applied against the "
                        "engineer group's currently active non-engineer "
                        "workers. This is not an agent_group affinity cap. "
                        "If omitted, Torque uses the group's stored "
                        "Engineer default."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider/backend override for new worker agents "
                        "created by this batch (for example 'codex' or "
                        "'claude-code'). If omitted, Torque uses the group's "
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
                        "milestones only, normal matches Torque defaults, "
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
            "reply via torque_reply when reply_required is true, which "
            "appears in the engineer's next event digest. By default "
            "Torque creates a visible follow-up task for the exchange and "
            "returns its task id. Set reply_required=false only for "
            "routine context/redirect messages that do not need an "
            "answer; Torque persists those on the worker's current task "
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
                        "Whether the agent must reply via torque_reply. "
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
        "name": "engineer_message_user",
        "description": (
            "Send a non-blocking durable direct message to the user-facing "
            "conversation panel. Use this for user-visible conversation or "
            "to reply to a `## Message from the User` injection. This is "
            "distinct from engineer_note, which is a board/status note; use "
            "engineer_ask instead for blocking human decisions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message content for the user.",
                },
                "reply_to_id": {
                    "type": "string",
                    "description": (
                        "Optional message id this is replying to. Torque "
                        "derives the user lane from the calling engineer."
                    ),
                },
                "context_task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional visible task ids/aliases to snapshot.",
                },
                "context_summary": {
                    "type": "string",
                    "description": "Optional concise context summary.",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": (
                        "Optional retry key; omit unless explicitly retrying "
                        "the same message."
                    ),
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "engineer_note",
        "description": (
            "Post a non-blocking note or soft question for the human, "
            "including next-wave proposals or status/context that "
            "should stay visible without pausing orchestration. "
            "Unlike engineer_ask, this does not pause event delivery or "
            "put Torque into awaiting-input mode."
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
            "Push an agent worktree branch, create or reuse a GitHub PR, "
            "and request a squash merge into the base branch. "
            "If configured nested ee/submodule changes are present, Torque "
            "first pushes the nested branch, opens/reuses a nested PR, "
            "merge-commit-merges that PR, and bumps the parent gitlink to the "
            "merged nested main SHA; zero-delta nested submodules create no "
            "nested PR. "
            "Non-interactive; on conflicts, run engineer_rebase then "
            "retry. Pass force_direct=true only for the explicit local "
            "direct-merge fallback; group engineer_merge_mode may reject "
            "force_direct=true or force the direct path. force_direct does "
            "not bypass the nested PR-first sequence for real ee deltas. "
            "Direct merges still "
            "honor the normal safety gates unless their explicit audited "
            "override flags are also supplied. "
            "Targets must be owned when ownership restriction is on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID/name with worktree. Omit when using worktree_path+branch driverless mode.",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Existing git worktree path for driverless mode (requires branch).",
                },
                "branch": {
                    "type": "string",
                    "description": "Existing local branch for driverless mode (requires worktree_path).",
                },
                "repo_root": {
                    "type": "string",
                    "description": "Optional main repository root for driverless mode.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Optional base branch for driverless merge/PR/remove.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Custom merge commit message. If omitted, "
                        "auto-generated from completed tasks."
                    ),
                },
                "pr_title": {
                    "type": "string",
                    "description": (
                        "GitHub PR title for the default PR/squash path. "
                        "Author a short imperative title that describes what "
                        "landed, not a generic branch/task-id-only label. "
                        "Also used as the squash commit subject. If omitted, "
                        "Torque derives a title from the merge message/task "
                        "history."
                    ),
                },
                "pr_body": {
                    "type": "string",
                    "description": (
                        "GitHub PR body for the default PR/squash path; "
                        "Markdown is allowed. Summarize what changed, why it "
                        "addresses the linked task(s), and what was tested. "
                        "Reference Torque task IDs such as TORQUE:123 where "
                        "applicable. Also used as the squash commit body, "
                        "with the PR URL appended during merge. If omitted, "
                        "Torque derives a body from the merge message/task "
                        "history."
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
                "force_direct": {
                    "type": "boolean",
                    "description": (
                        "Bypass the default GitHub PR/squash-merge flow "
                        "and use the direct local worktree merge path. This "
                        "may be rejected when the group locks "
                        "engineer_merge_mode='pr'. It does not bypass "
                        "the nested ee/submodule PR-first flow for real "
                        "submodule deltas, and it never direct-pushes nested "
                        "main as an emergency override. It does not bypass "
                        "stale-base, reviewed-boundary, or "
                        "sibling-divergence safety gates; "
                        "combine with force or "
                        "force_stale_base only when intentionally accepting "
                        "those risks."
                    ),
                },
                "force_stale_base": {
                    "type": "boolean",
                    "description": (
                        "Bypass the stale-base merge safety gate. Use only "
                        "after intentionally accepting that the branch forked "
                        "before the current base and re-running review is not "
                        "desired. `force=true` is the preferred override and "
                        "also bypasses this gate."
                    ),
                },
                "force_boundary_mismatch": {
                    "type": "boolean",
                    "description": (
                        "Bypass only the reviewed-boundary tip mismatch gate. "
                        "Use only after an Engineer verifies the branch tip "
                        "against the last reviewed boundary; Torque records "
                        "a workflow-breach audit with actor, reason, "
                        "boundary SHA, and tip SHA. This is separate from "
                        "force_stale_base/force and does not bypass stale-base "
                        "or sibling-divergence gates."
                    ),
                },
                "boundary_mismatch_reason": {
                    "type": "string",
                    "description": (
                        "Audit reason to record when "
                        "force_boundary_mismatch=true."
                    ),
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "Bypass the stale-base and sibling-branch divergence "
                        "safety gates. Use only after rebasing or otherwise "
                        "intentionally accepting stale-base risk, and after "
                        "diffing sibling review/implement branches and "
                        "accepting any commits not present on the merge "
                        "target."
                    ),
                },
            },
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
                    "description": "Agent ID or name with a worktree. Omit when using worktree_path+branch driverless mode.",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Existing git worktree path for driverless mode (requires branch).",
                },
                "branch": {
                    "type": "string",
                    "description": "Existing local branch for driverless mode (requires worktree_path).",
                },
                "repo_root": {
                    "type": "string",
                    "description": "Optional main repository root for driverless mode.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Optional base branch for driverless mode.",
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
            "only agents created by this Engineer can be targeted. Refuses "
            "active/fresh attached agents and verifies git post-state before "
            "reporting success."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent ID or name with a worktree. Omit when using worktree_path+branch orphan mode.",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Existing orphaned git worktree path to remove safely (requires branch).",
                },
                "branch": {
                    "type": "string",
                    "description": "Existing local branch for orphan removal (requires worktree_path).",
                },
                "repo_root": {
                    "type": "string",
                    "description": "Optional main repository root for orphan removal.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Base branch used to verify branch is merged before deletion.",
                },
                "delete_branch": {
                    "type": "boolean",
                    "description": "Delete the local branch with git branch -d after safe removal. Defaults true; false preserves it.",
                },
            },
        },
    },
    {
        "name": "engineer_worktree_adopt",
        "description": "Attach a stopped/idle visible agent to an existing orphaned worktree+branch without creating or moving branches.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Stopped/idle agent to attach."},
                "worktree_path": {"type": "string", "description": "Existing git worktree path."},
                "branch": {"type": "string", "description": "Existing local branch checked out in the worktree."},
                "repo_root": {"type": "string", "description": "Optional main repository root."},
                "base_branch": {"type": "string", "description": "Optional base branch for the adopted worktree."},
                "relaunch": {"type": "boolean", "description": "Relaunch the agent after attaching. Default false."},
            },
            "required": ["agent", "worktree_path", "branch"],
        },
    },
    {
        "name": "engineer_worktree_advance_boundary",
        "description": "Advance the latest open worktree boundary to a new tip only after Torque machine-verifies exactly one configured nested-submodule gitlink-only commit. verification_note is audit metadata, not authorization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent with worktree. Omit when using worktree_path+branch."},
                "worktree_path": {"type": "string", "description": "Existing git worktree path for driverless mode."},
                "branch": {"type": "string", "description": "Existing local branch for driverless mode."},
                "repo_root": {"type": "string", "description": "Optional main repository root."},
                "base_branch": {"type": "string", "description": "Optional base branch."},
                "expected_previous_head": {"type": "string", "description": "Boundary commit SHA currently recorded."},
                "expected_new_head": {"type": "string", "description": "Expected current branch HEAD; defaults to HEAD if omitted."},
                "verification_note": {"type": "string", "description": "Required audit explanation of the external/mechanical verification."},
                "reason": {"type": "string", "description": "Audit reason. Defaults to verified_mechanical_gitlink."},
            },
            "required": ["expected_previous_head", "verification_note"],
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
                        "'project' (default) or 'user' (~/.torque/"
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
