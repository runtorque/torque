//! Weaver MCP tool schemas. Ported subset of `loom/mcp_weaver_tools`.

use serde_json::{json, Value};

pub fn weaver_tool_specs() -> Vec<Value> {
    vec![
        json!({
            "name": "weaver_board_summary",
            "description": "Return a compact board overview for the weaver's group, including lane counts, task-health rollups, hints, agent status, and compact stream summaries.",
            "inputSchema": {"type": "object", "properties": {}}
        }),
        json!({
            "name": "weaver_session_map",
            "description": "Return a deterministic structured Session Map for the weaver's group: streams, asks, human gates, unhealthy tasks, verification gates, agents, queued follow-up work, journal, and hints.",
            "inputSchema": {"type": "object", "properties": {}}
        }),
        json!({
            "name": "weaver_streams_list",
            "description": "List computed branch/worktree streams for the weaver's group.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {"type": "string"},
                    "branch": {"type": "string"},
                    "repo_root": {"type": "string"},
                    "include_orphaned": {"type": "boolean"}
                }
            }
        }),
        json!({
            "name": "weaver_stream_show",
            "description": "Show one computed stream by stream id, branch identity, or related task id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "stream": {"type": "string"},
                    "task": {"type": "string"},
                    "branch": {"type": "string"},
                    "repo_root": {"type": "string"}
                }
            }
        }),
        json!({
            "name": "weaver_board_list",
            "description": "List tasks on the board grouped by lane with optional filters by lane, label, health state, or text search.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "lane": {"type": "string"},
                    "label": {"type": "string"},
                    "health": {"type": "string"},
                    "search": {"type": "string"}
                }
            }
        }),
        json!({
            "name": "weaver_task_show",
            "description": "Show full details for a task by id or alias, including messages and pipeline chain when applicable.",
            "inputSchema": {
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"]
            }
        }),
        json!({
            "name": "weaver_agents_list",
            "description": "List visible active agents for the weaver's group.",
            "inputSchema": {"type": "object", "properties": {}}
        }),
        json!({
            "name": "weaver_agent_show",
            "description": "Show detailed information about one visible agent.",
            "inputSchema": {
                "type": "object",
                "properties": {"agent": {"type": "string"}},
                "required": ["agent"]
            }
        }),
        json!({
            "name": "weaver_actions_list",
            "description": "List available actions with scope and metadata.",
            "inputSchema": {"type": "object", "properties": {"group": {"type": "string"}}}
        }),
        json!({
            "name": "weaver_action_show",
            "description": "Show full details for one action.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "group": {"type": "string"}},
                "required": ["name"]
            }
        }),
        json!({
            "name": "weaver_task_create",
            "description": "Create a new task on the board.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "group": {"type": "string"},
                    "lane": {"type": "string"},
                    "action": {"type": "string"},
                    "action_vars": {"type": "object"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "verification_mode": {"type": "string"},
                    "verification_state": {"type": "string"},
                    "verification_notes": {"type": "string"},
                    "verification_summary": {"type": "object"}
                },
                "required": ["title"]
            }
        }),
        json!({
            "name": "weaver_task_edit",
            "description": "Edit fields on an existing task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "action": {"type": "string"},
                    "action_vars": {"type": "object"},
                    "verification_mode": {"type": "string"},
                    "verification_state": {"type": "string"},
                    "verification_notes": {"type": "string"},
                    "verification_summary": {"type": "object"}
                },
                "required": ["task"]
            }
        }),
        json!({
            "name": "weaver_task_verify",
            "description": "Record verification status on a task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "mode": {"type": "string"},
                    "state": {"type": "string"},
                    "notes": {"type": "string"},
                    "tests_run": {"type": "string"},
                    "human_validation_pending": {"type": "string"},
                    "deploy_needed": {"type": "boolean"},
                    "attempted": {"type": "boolean"},
                    "smoke": {"type": "string"}
                },
                "required": ["task"]
            }
        }),
        json!({
            "name": "weaver_task_move",
            "description": "Move a task to a different lane on the board.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "lane": {"type": "string"}
                },
                "required": ["task", "lane"]
            }
        }),
        json!({
            "name": "weaver_task_dispatch",
            "description": "Dispatch a task to an existing or new agent.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "agent": {"type": "string"},
                    "name": {"type": "string"},
                    "agent_type": {"type": "string"},
                    "command": {"type": "string"},
                    "model": {"type": "string"},
                    "reasoning_effort": {"type": "string"}
                },
                "required": ["task"]
            }
        }),
        json!({
            "name": "weaver_batch_dispatch",
            "description": "Dispatch a planned batch of tasks with an explicit or default concurrency cap.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string"},
                                "agent_group": {"type": "string"}
                            },
                            "required": ["task"]
                        }
                    },
                    "max_concurrent": {"type": "integer"}
                },
                "required": ["tasks"]
            }
        }),
        json!({
            "name": "weaver_task_resolve",
            "description": "Resolve an ask task by providing an answer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "answer": {"type": "string"}
                },
                "required": ["task", "answer"]
            }
        }),
        json!({
            "name": "weaver_events",
            "description": "Read recent events for the weaver's group.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since_id": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "types": {"type": "array", "items": {"type": "string"}}
                }
            }
        }),
        json!({
            "name": "weaver_launch_settings",
            "description": "Update the designated Weaver's persisted launch settings.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string"},
                    "command": {"type": "string"},
                    "model": {"type": "string"},
                    "reasoning_effort": {"type": "string"}
                }
            }
        }),
        json!({
            "name": "weaver_notifications",
            "description": "Configure digest noise using quiet/normal/noisy presets or explicit interval/event overrides.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "preset": {"type": "string", "enum": ["quiet", "normal", "noisy"]},
                    "digest_verbosity": {"type": "string", "enum": ["compact", "balanced", "detailed"]},
                    "push_interval": {"type": "integer"},
                    "max_interval": {"type": "integer"},
                    "heartbeat_interval": {"type": "integer"},
                    "enable": {"type": "array", "items": {"type": "string"}},
                    "disable": {"type": "array", "items": {"type": "string"}}
                }
            }
        }),
        json!({
            "name": "weaver_resume",
            "description": "Resume event delivery after a weaver_ask.",
            "inputSchema": {"type": "object", "properties": {}}
        }),
        json!({
            "name": "weaver_journal",
            "description": "Append an entry to the weaver's persistent journal.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["decision", "observation", "checkpoint", "plan"]},
                    "entry": {"type": "string"}
                },
                "required": ["type", "entry"]
            }
        }),
        json!({
            "name": "weaver_journal_read",
            "description": "Read recent journal entries.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tail": {"type": "integer"},
                    "type": {"type": "string", "enum": ["decision", "observation", "checkpoint", "plan"]}
                }
            }
        }),
        json!({
            "name": "weaver_ask",
            "description": "Ask the human a blocking question and pause event delivery until answered.",
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"]
            }
        }),
        json!({
            "name": "weaver_note",
            "description": "Post a non-blocking note or soft question for the human without pausing delivery.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "kind": {"type": "string", "enum": ["note", "question"]}
                },
                "required": ["message"]
            }
        }),
        json!({
            "name": "weaver_agent_message",
            "description": "Send a message to an agent's terminal and create a visible follow-up task for the exchange.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string"},
                    "message": {"type": "string"}
                },
                "required": ["agent", "message"]
            }
        }),
        json!({
            "name": "weaver_agent_close",
            "description": "Close an agent and remove it from the group.",
            "inputSchema": {
                "type": "object",
                "properties": {"agent": {"type": "string"}},
                "required": ["agent"]
            }
        }),
        json!({
            "name": "weaver_agent_relaunch",
            "description": "Relaunch a stopped agent.",
            "inputSchema": {
                "type": "object",
                "properties": {"agent": {"type": "string"}},
                "required": ["agent"]
            }
        }),
    ]
}
