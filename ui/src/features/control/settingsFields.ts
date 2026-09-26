// Field labels, choices and constraints audited against the classic settings forms
// and state dataclasses. Values continue to come from the daemon.
export interface SettingField { label: string; options?: { value: string; label: string }[]; min?: string; max?: string; step?: string; placeholder?: string; description?: string; kind?: string }
export const settingFields: Record<string, SettingField> = {
  ship_direct_max: { label: 'Ship direct max', kind: 'int', min: '0', description: 'Maximum changed lines allowed for direct shipping by the review gate.' },
  review_default_above: { label: 'Review default above', kind: 'int', min: '0', description: 'Changed-line threshold above which review is the default.' },
  self_review_bypass_allowed: { label: 'Self review bypass allowed', kind: 'bool' },
  github_project_number: { label: 'GitHub project number', kind: 'int', min: '0' },
  github_lane_status_map: { label: 'GitHub lane status map', kind: 'dict' },
  github_assignee_map: { label: 'GitHub assignee map', kind: 'dict' },
  "default_command": {
    "label": "Default command",
    "placeholder": "claude",
    "description": "empty = use config.DEFAULT_COMMAND (env var fallback)",
    "kind": "str"
  },
  "filter_by_window": {
    "label": "Filter by window",
    "kind": "bool",
    "description": "global default for window filtering"
  },
  "focus_new_tabs": {
    "label": "Focus new tabs",
    "description": "switch focus to newly created tabs",
    "kind": "bool"
  },
  "focus_on_click": {
    "label": "Focus on click",
    "description": "single click also focuses the terminal",
    "kind": "bool"
  },
  "max_pipeline_depth": {
    "label": "Max pipeline depth",
    "min": "0",
    "description": "0 = unlimited",
    "kind": "int"
  },
  "max_event_log": {
    "label": "Max event log",
    "min": "50",
    "max": "10000",
    "description": "max persisted panel events",
    "kind": "int"
  },
  "github_repo": {
    "label": "Github repo",
    "placeholder": "owner/repo"
  },
  "github_project_owner": {
    "label": "Github project owner",
    "placeholder": "org or user login"
  },
  "github_project_status_field": {
    "label": "Github project status field",
    "placeholder": "Status"
  },
  "board_sync_enabled": {
    "label": "Board sync enabled",
    "kind": "bool"
  },
  "provider": {
    "label": "Provider",
    "options": [
      {
        "value": "none",
        "label": "None"
      },
      {
        "value": "github",
        "label": "GitHub"
      }
    ]
  },
  "default_agent_template": {
    "label": "Default agent template",
    "kind": "str"
  },
  "agent_provider": {
    "label": "Agent provider",
    "description": "adapter name (\"claude-code\", \"codex\", etc.) \u2014 empty = use default",
    "kind": "str"
  },
  "agent_boot_command": {
    "label": "Agent boot command",
    "placeholder": "e.g. my-agent-cli",
    "description": "override default boot command (e.g. \"codex\")",
    "kind": "str"
  },
  "agent_model": {
    "label": "Agent model",
    "placeholder": "Enter a model ID",
    "description": "default model override when provider supports it",
    "kind": "str"
  },
  "agent_reasoning_effort": {
    "label": "Agent reasoning effort",
    "description": "default reasoning-effort override",
    "kind": "str"
  },
  "agent_fast_mode": {
    "label": "Agent fast mode",
    "options": [
      {
        "value": "inherit",
        "label": "Inherit"
      },
      {
        "value": "on",
        "label": "Fast on"
      },
      {
        "value": "off",
        "label": "Fast off"
      }
    ],
    "description": "Codex Fast: inherit | on | off",
    "kind": "str"
  },
  "worker_provider": {
    "label": "Worker provider",
    "description": "adapter override for workers (empty = use group default)",
    "kind": "str"
  },
  "worker_boot_command": {
    "label": "Worker boot command",
    "placeholder": "Inherit group command",
    "description": "boot command override for workers (empty = use group default)",
    "kind": "str"
  },
  "worker_model": {
    "label": "Worker model",
    "placeholder": "Enter a model ID",
    "description": "model override for workers (empty = use group default)",
    "kind": "str"
  },
  "worker_reasoning_effort": {
    "label": "Worker reasoning effort",
    "description": "reasoning override for workers (empty = use group default)",
    "kind": "str"
  },
  "worker_fast_mode": {
    "label": "Worker fast mode",
    "options": [
      {
        "value": "inherit",
        "label": "Inherit shared default"
      },
      {
        "value": "on",
        "label": "Fast on"
      },
      {
        "value": "off",
        "label": "Fast off"
      }
    ],
    "description": "Codex Fast: inherit | on | off",
    "kind": "str"
  },
  "agent_directory": {
    "label": "Agent directory",
    "placeholder": "Inherit group directory",
    "kind": "str"
  },
  "agent_shell": {
    "label": "Agent shell",
    "options": [
      {
        "value": "",
        "label": "Inherit group shell"
      },
      {
        "value": "zsh",
        "label": "zsh"
      },
      {
        "value": "bash",
        "label": "bash"
      },
      {
        "value": "fish",
        "label": "fish"
      }
    ],
    "kind": "str"
  },
  "engineer_merge_mode": {
    "label": "Engineer merge mode",
    "options": [
      {
        "value": "pr",
        "label": "Pull request (default)"
      },
      {
        "value": "direct",
        "label": "Direct local"
      },
      {
        "value": "engineer-choice",
        "label": "Engineer choice"
      }
    ],
    "description": "pr | direct | engineer-choice",
    "kind": "str"
  },
  "worktree_merge_cleanup": {
    "label": "Worktree merge cleanup",
    "options": [
      {
        "value": "keep",
        "label": "Keep worker and worktree (default / warm)"
      },
      {
        "value": "auto_sweep",
        "label": "Auto-sweep merged branch and worktree"
      },
      {
        "value": "close",
        "label": "Close worker only"
      },
      {
        "value": "remove",
        "label": "Delete worktree only"
      },
      {
        "value": "close_remove",
        "label": "Close worker and delete worktree"
      }
    ],
    "description": "keep | close | remove | close_remove | auto_sweep",
    "kind": "str"
  },
  "engineer_provider": {
    "label": "Engineer provider",
    "description": "adapter name override (empty = use group default)",
    "kind": "str"
  },
  "engineer_boot_command": {
    "label": "Engineer boot command",
    "placeholder": "Inherit group command",
    "description": "boot command override (empty = use provider default)",
    "kind": "str"
  },
  "engineer_model": {
    "label": "Engineer model",
    "placeholder": "Enter a model ID",
    "description": "model override for the designated engineer",
    "kind": "str"
  },
  "engineer_reasoning_effort": {
    "label": "Engineer reasoning effort",
    "description": "reasoning-effort override for the designated engineer",
    "kind": "str"
  },
  "engineer_fast_mode": {
    "label": "Engineer fast mode",
    "options": [
      {
        "value": "inherit",
        "label": "Inherit shared default"
      },
      {
        "value": "on",
        "label": "Fast on"
      },
      {
        "value": "off",
        "label": "Fast off"
      }
    ],
    "description": "Codex Fast: inherit | on | off",
    "kind": "str"
  },
  "engineer_directory": {
    "label": "Engineer directory",
    "placeholder": "Inherit runtime directory",
    "description": "directory override for the designated engineer",
    "kind": "str"
  },
  "engineer_shell": {
    "label": "Engineer shell",
    "options": [
      {
        "value": "",
        "label": "Inherit runtime shell"
      },
      {
        "value": "zsh",
        "label": "zsh"
      },
      {
        "value": "bash",
        "label": "bash"
      },
      {
        "value": "fish",
        "label": "fish"
      }
    ],
    "description": "shell override for the designated engineer",
    "kind": "str"
  },
  "custom_instructions": {
    "label": "Custom instructions",
    "placeholder": "Instructions appended after Torque's built-in Engineer policy...",
    "description": "user-defined instructions appended to engineer system prompt",
    "kind": "str"
  },
  "autonomy_mode": {
    "label": "Autonomy mode",
    "options": [
      {
        "value": "suggest_only",
        "label": "Suggest only"
      },
      {
        "value": "dispatch_when_clear",
        "label": "Dispatch when clear"
      },
      {
        "value": "aggressive_auto_continue",
        "label": "Aggressive auto-continue"
      }
    ],
    "description": "suggest_only | dispatch_when_clear | aggressive_auto_continue",
    "kind": "str"
  },
  "wave_size_preference": {
    "label": "Wave size preference",
    "options": [
      {
        "value": "small",
        "label": "Small reviewable waves"
      },
      {
        "value": "balanced",
        "label": "Balanced waves"
      },
      {
        "value": "large",
        "label": "Fill available capacity"
      }
    ],
    "description": "small | balanced | large",
    "kind": "str"
  },
  "same_agent_follow_up_preference": {
    "label": "Same agent follow up preference",
    "options": [
      {
        "value": "balanced",
        "label": "Balanced"
      },
      {
        "value": "prefer_same_agent",
        "label": "Prefer same worker"
      },
      {
        "value": "prefer_fresh_agent",
        "label": "Prefer fresh worker"
      }
    ],
    "description": "balanced | prefer_same_agent | prefer_fresh_agent",
    "kind": "str"
  },
  "digest_verbosity": {
    "label": "Digest verbosity",
    "options": [
      {
        "value": "compact",
        "label": "Compact"
      },
      {
        "value": "balanced",
        "label": "Balanced"
      },
      {
        "value": "detailed",
        "label": "Detailed"
      }
    ],
    "description": "compact | balanced | detailed",
    "kind": "str"
  },
  "escalation_style": {
    "label": "Escalation style",
    "options": [
      {
        "value": "ask_early",
        "label": "Ask early"
      },
      {
        "value": "note_then_ask",
        "label": "Note first, ask when blocked"
      },
      {
        "value": "keep_moving",
        "label": "Keep moving unless blocked"
      }
    ],
    "description": "ask_early | note_then_ask | keep_moving",
    "kind": "str"
  },
  "architect_provider": {
    "label": "Architect provider",
    "kind": "str"
  },
  "architect_boot_command": {
    "label": "Architect boot command",
    "placeholder": "Inherit group command",
    "kind": "str"
  },
  "architect_model": {
    "label": "Architect model",
    "placeholder": "Enter a model ID",
    "kind": "str"
  },
  "architect_reasoning_effort": {
    "label": "Architect reasoning effort",
    "kind": "str"
  },
  "architect_fast_mode": {
    "label": "Architect fast mode",
    "options": [
      {
        "value": "inherit",
        "label": "Inherit shared default"
      },
      {
        "value": "on",
        "label": "Fast on"
      },
      {
        "value": "off",
        "label": "Fast off"
      }
    ],
    "description": "Codex Fast: inherit | on | off",
    "kind": "str"
  },
  "architect_directory": {
    "label": "Architect directory",
    "placeholder": "Inherit runtime directory",
    "kind": "str"
  },
  "architect_shell": {
    "label": "Architect shell",
    "options": [
      {
        "value": "",
        "label": "Inherit runtime shell"
      },
      {
        "value": "zsh",
        "label": "zsh"
      },
      {
        "value": "bash",
        "label": "bash"
      },
      {
        "value": "fish",
        "label": "fish"
      }
    ],
    "kind": "str"
  },
  "architect_custom_instructions": {
    "label": "Architect custom instructions",
    "placeholder": "Instructions appended after Torque's built-in Architect policy...",
    "kind": "str"
  },
  "architect_autonomy_mode": {
    "label": "Architect autonomy mode",
    "options": [
      {
        "value": "dispatch_freely",
        "label": "Dispatch freely"
      },
      {
        "value": "dispatch_after_confirm",
        "label": "Dispatch after confirm"
      },
      {
        "value": "ask_always",
        "label": "Ask always"
      }
    ],
    "kind": "str"
  },
  "architect_digest_verbosity": {
    "label": "Architect digest verbosity",
    "options": [
      {
        "value": "terse",
        "label": "Terse"
      },
      {
        "value": "balanced",
        "label": "Balanced"
      },
      {
        "value": "verbose",
        "label": "Verbose"
      }
    ],
    "kind": "str"
  },
  "architect_journal_checkpoint_frequency": {
    "label": "Architect journal checkpoint frequency",
    "kind": "str"
  },
  "push_interval": {
    "label": "Push interval",
    "options": [
      {
        "value": "10",
        "label": "10s"
      },
      {
        "value": "30",
        "label": "30s"
      },
      {
        "value": "60",
        "label": "60s"
      },
      {
        "value": "120",
        "label": "120s"
      },
      {
        "value": "300",
        "label": "300s"
      }
    ],
    "description": "seconds between digest pushes (min: 10)",
    "kind": "int"
  },
  "max_interval": {
    "label": "Max interval",
    "options": [
      {
        "value": "60",
        "label": "60s"
      },
      {
        "value": "120",
        "label": "120s"
      },
      {
        "value": "300",
        "label": "300s"
      },
      {
        "value": "600",
        "label": "600s"
      }
    ],
    "description": "max seconds between normal digest pushes",
    "kind": "int"
  },
  "heartbeat_interval": {
    "label": "Heartbeat interval",
    "options": [
      {
        "value": "0",
        "label": "Off"
      },
      {
        "value": "60",
        "label": "1 min"
      },
      {
        "value": "120",
        "label": "2 min"
      },
      {
        "value": "300",
        "label": "5 min"
      },
      {
        "value": "600",
        "label": "10 min"
      }
    ],
    "description": "quiet seconds before idle heartbeat digest (0 = off)",
    "kind": "int"
  },
  "default_directory": {
    "label": "Default directory",
    "placeholder": "/path/to/project",
    "kind": "str"
  },
  "shell": {
    "label": "Shell",
    "options": [
      {
        "value": "",
        "label": "System default"
      },
      {
        "value": "zsh",
        "label": "zsh"
      },
      {
        "value": "bash",
        "label": "bash"
      },
      {
        "value": "fish",
        "label": "fish"
      }
    ],
    "kind": "str"
  },
  "env_vars": {
    "label": "Env vars",
    "placeholder": "FOO=bar\nBAZ=qux",
    "kind": "dict[str, str]"
  },
  "env_file": {
    "label": "Env file",
    "placeholder": ".env",
    "kind": "str"
  },
  "max_agents": {
    "label": "Max agents",
    "min": "0",
    "max": "100",
    "kind": "int"
  },
  "collapsed_default": {
    "label": "Collapsed default",
    "kind": "bool"
  },
  "agent_env_vars": {
    "label": "Agent env vars",
    "placeholder": "FOO=bar\nBAZ=qux",
    "kind": "dict[str, str]"
  },
  "agent_env_file": {
    "label": "Agent env file",
    "placeholder": "Inherit group env file",
    "kind": "str"
  },
  "git_worktree": {
    "label": "Git worktree",
    "options": [
      {
        "value": "shared",
        "label": "Shared group checkout"
      },
      {
        "value": "isolated",
        "label": "Isolated worktree (recommended)"
      }
    ],
    "kind": "bool"
  },
  "worktree_base_dir": {
    "label": "Worktree base dir",
    "placeholder": ".torque/worktrees",
    "description": "directory for worktrees (relative to repo)",
    "kind": "str"
  },
  "worktree_base_branch": {
    "label": "Worktree base branch",
    "placeholder": "main",
    "description": "branch to fork from (empty = current HEAD)",
    "kind": "str"
  },
  "worktree_merge_squash": {
    "label": "Worktree merge squash",
    "options": [
      {
        "value": "preserve",
        "label": "Preserve commits"
      },
      {
        "value": "squash",
        "label": "Squash into one commit"
      }
    ],
    "description": "squash commits when merging to main",
    "kind": "bool"
  },
  "worktree_merge_preserve_diff": {
    "label": "Worktree merge preserve diff",
    "description": "save the pre-merge patch on the latest boundary task",
    "kind": "bool"
  },
  "worktree_symlink_gitignored_paths": {
    "label": "Worktree symlink gitignored paths",
    "description": "symlink gitignored files/dirs from repo root into worktrees",
    "kind": "bool"
  },
  "agent_session_resume": {
    "label": "Agent session resume",
    "description": "resume session on relaunch",
    "kind": "bool"
  },
  "agent_idle_timeout": {
    "label": "Agent idle timeout",
    "min": "0",
    "max": "60",
    "description": "minutes before flagging agent as stuck (0=disable)",
    "kind": "int"
  },
  "engineer_behavior_requires_user_approval": {
    "label": "Engineer behavior requires user approval",
    "kind": "bool"
  },
  "notifications": {
    "label": "Notifications",
    "kind": "bool"
  },
  "notify_on_finish": {
    "label": "Notify on finish",
    "kind": "bool"
  },
  "notify_on_error": {
    "label": "Notify on error",
    "kind": "bool"
  },
  "notify_on_attention": {
    "label": "Notify on attention",
    "kind": "bool"
  },
  "board_default_action": {
    "label": "Board default action",
    "placeholder": "feature/implement",
    "description": "default action for new tasks",
    "kind": "str"
  },
  "board_default_labels": {
    "label": "Board default labels",
    "placeholder": "frontend, urgent",
    "description": "default labels for new tasks",
    "kind": "list[str]"
  },
  "board_default_lane": {
    "label": "Board default lane",
    "placeholder": "First board lane",
    "description": "default lane for new tasks (empty = first lane)",
    "kind": "str"
  },
  "dispatch_lane": {
    "label": "Dispatch lane",
    "placeholder": "In Progress",
    "description": "lane for dispatched tasks",
    "kind": "str"
  },
  "board_sync_provider": {
    "label": "Board sync provider",
    "options": [
      {
        "value": "none",
        "label": "None"
      },
      {
        "value": "github",
        "label": "GitHub"
      }
    ],
    "description": "none | github (future providers reserved)",
    "kind": "str"
  },
  "github_project_id": {
    "label": "Github project id"
  },
  "github_close_issues_via_pr": {
    "label": "Github close issues via pr"
  },
  "github_create_missing_labels": {
    "label": "Github create missing labels"
  },
  "engineer_can_override_worker_provider": {
    "label": "Engineer can override worker provider",
    "description": "expose worker provider override in Engineer dispatch tools",
    "kind": "bool"
  },
  "default_worker_concurrency": {
    "label": "Default worker concurrency",
    "options": [
      {
        "value": "1",
        "label": "1 worker"
      },
      {
        "value": "2",
        "label": "2 workers"
      },
      {
        "value": "3",
        "label": "3 workers"
      },
      {
        "value": "4",
        "label": "4 workers"
      },
      {
        "value": "5",
        "label": "5 workers"
      },
      {
        "value": "6",
        "label": "6 workers"
      },
      {
        "value": "8",
        "label": "8 workers"
      }
    ],
    "description": "default max_concurrent for dispatch waves",
    "kind": "int"
  },
  "architect_push_interval": {
    "label": "Architect push interval",
    "options": [
      {
        "value": "60",
        "label": "60s"
      },
      {
        "value": "120",
        "label": "120s"
      },
      {
        "value": "300",
        "label": "300s"
      },
      {
        "value": "600",
        "label": "600s"
      },
      {
        "value": "900",
        "label": "900s"
      }
    ],
    "kind": "int"
  },
  "architect_max_interval": {
    "label": "Architect max interval",
    "options": [
      {
        "value": "120",
        "label": "120s"
      },
      {
        "value": "300",
        "label": "300s"
      },
      {
        "value": "600",
        "label": "600s"
      },
      {
        "value": "1200",
        "label": "1200s"
      },
      {
        "value": "1800",
        "label": "1800s"
      }
    ],
    "kind": "int"
  },
  "architect_heartbeat_interval": {
    "label": "Architect heartbeat interval",
    "options": [
      {
        "value": "0",
        "label": "Off"
      },
      {
        "value": "300",
        "label": "5 min"
      },
      {
        "value": "600",
        "label": "10 min"
      },
      {
        "value": "1200",
        "label": "20 min"
      },
      {
        "value": "1800",
        "label": "30 min"
      }
    ],
    "kind": "int"
  },
  "architect_suppress_empty_digests": {
    "label": "Architect suppress empty digests",
    "kind": "bool"
  },
  "default_terminal_backend": {
    "label": "Default terminal backend",
    "kind": "str"
  },
  "profile": {
    "label": "Profile",
    "kind": "str"
  },
  "tab_color": {
    "label": "Tab color",
    "kind": "str"
  },
  "agent_terminal_profile": {
    "label": "Agent terminal profile",
    "kind": "str"
  },
  "agent_tab_color": {
    "label": "Agent tab color",
    "kind": "str"
  },
  "worktree_auto_checkpoint": {
    "label": "Worktree auto checkpoint",
    "description": "auto-checkpoint on agent stop",
    "kind": "bool"
  },
  "checkpoint_on_progress": {
    "label": "Checkpoint on progress",
    "description": "auto-checkpoint on torque ai progress/done",
    "kind": "bool"
  },
  "worktree_merge_instructions": {
    "label": "Worktree merge instructions",
    "description": "additional instructions appended to merge prompt",
    "kind": "str"
  },
  "worktree_symlinks": {
    "label": "Worktree symlinks",
    "description": "repo-relative paths or glob patterns to symlink from repo root",
    "kind": "list[str]"
  },
  "worktree_submodules": {
    "label": "Worktree submodules",
    "description": "repo-relative submodule paths to materialize as nested linked worktrees",
    "kind": "list[str]"
  },
  "guidance_hint_cadence": {
    "label": "Guidance hint cadence",
    "min": "0", "max": "100",
    "description": "Show guidance on the first occurrence and every N occurrences after that (0–100). Set 0 to show it every time.",
    "kind": "int"
  },
  "context_default_ttl_days": {
    "label": "Context default ttl days",
    "min": "1", "max": "60",
    "description": "Default lifetime of new Shared Context entries, from 1 to 60 days.",
    "kind": "int"
  },
  "engineer_hint_snoozes": {
    "label": "Engineer hint snoozes",
    "description": "hint fingerprint -> unix expiry",
    "kind": "dict[str, float]"
  },
  "terminal_name_prefix": {
    "label": "Terminal name prefix",
    "kind": "str"
  },
  "terminal_boot_command": {
    "label": "Terminal boot command",
    "kind": "str"
  },
  "terminal_command_args": {
    "label": "Terminal command args",
    "kind": "str"
  },
  "terminal_init_script": {
    "label": "Terminal init script",
    "kind": "str"
  },
  "terminal_directory": {
    "label": "Terminal directory",
    "kind": "str"
  },
  "terminal_profile": {
    "label": "Terminal profile",
    "kind": "str"
  },
  "terminal_shell": {
    "label": "Terminal shell",
    "kind": "str"
  },
  "terminal_tab_color": {
    "label": "Terminal tab color",
    "kind": "str"
  },
  "terminal_env_vars": {
    "label": "Terminal env vars",
    "kind": "dict[str, str]"
  },
  "terminal_env_file": {
    "label": "Terminal env file",
    "kind": "str"
  },
  "terminal_always_custom_dialog": {
    "label": "Terminal always custom dialog",
    "kind": "bool"
  },
  "terminal_close_on_disconnect": {
    "label": "Terminal close on disconnect",
    "description": "remove terminal from Torque when tab closed",
    "kind": "bool"
  },
  "board_sync_github": {
    "label": "Board sync github",
    "description": "GitHub adapter settings",
    "kind": "dict"
  },
  "engineer_agent_id": {
    "label": "Engineer agent id",
    "description": "designated engineer agent for this group",
    "kind": "str"
  },
  "default_engineer_specializations": {
    "label": "Default engineer specializations",
    "description": "ordered, applied at engineer creation",
    "kind": "list[str]"
  },
  "architect_profile": {
    "label": "Architect profile",
    "kind": "str"
  },
  "architect_tab_color": {
    "label": "Architect tab color",
    "kind": "str"
  },
  "architect_enabled_events": {
    "label": "Architect enabled events",
    "kind": "list[str]"
  },
  "architect_review_gate_thresholds": {
    "label": "Architect review gate thresholds",
    "kind": "dict"
  },
  "group": {
    "label": "Group",
    "kind": "str"
  },
  "paused": {
    "label": "Paused",
    "description": "user paused event pushes",
    "kind": "bool"
  },
  "restrict_to_created_agents": {
    "label": "Restrict to created agents",
    "description": "limit Engineer agent visibility/control to its own created agents",
    "kind": "bool"
  },
  "pending_question": {
    "label": "Pending question",
    "description": "question awaiting human reply (non-empty = awaiting input)",
    "kind": "str"
  },
  "pending_question_set_at": {
    "label": "Pending question set at",
    "description": "unix timestamp when pending_question was set",
    "kind": "float"
  },
  "pending_question_actor_id": {
    "label": "Pending question actor id",
    "description": "engineer who set pending_question",
    "kind": "str"
  },
  "pending_note": {
    "label": "Pending note",
    "description": "non-blocking note/question for the human",
    "kind": "str"
  },
  "pending_note_kind": {
    "label": "Pending note kind",
    "description": "\"note\" | \"question\" | \"\"",
    "kind": "str"
  },
  "pending_note_set_at": {
    "label": "Pending note set at",
    "description": "unix timestamp when pending_note was set",
    "kind": "float"
  },
  "pending_note_actor_id": {
    "label": "Pending note actor id",
    "description": "engineer who set pending_note",
    "kind": "str"
  },
  "engineer_profile": {
    "label": "Engineer profile",
    "description": "iTerm profile override for the designated engineer",
    "kind": "str"
  },
  "engineer_tab_color": {
    "label": "Engineer tab color",
    "description": "tab color override for the designated engineer",
    "kind": "str"
  },
  "enabled_events": {
    "label": "Enabled events",
    "description": "optional events (mandatory always on)",
    "kind": "list[str]"
  },
  "xterm_scrollback": {
    "min": "100", "max": "100000",
    "label": "Xterm scrollback",
    "description": "embedded xterm.js history lines",
    "kind": "int"
  },
  "default_lanes": {
    "label": "Default lanes",
    "kind": "list[str]"
  },
  "keybindings": {
    "label": "Keybindings",
    "kind": "dict[str, dict]"
  },
  "metrics_enabled": {
    "label": "Metrics enabled",
    "description": "enable low-overhead daemon metrics ticks/history",
    "kind": "bool"
  },
  "event_ingest_max_rows": {
    "label": "Event ingest max rows",
    "min": "1",
    "description": "Keep at most this many ingested events. Minimum 1; reducing this limit can remove older records.",
    "kind": "int"
  },
  "event_ingest_max_days": {
    "label": "Event ingest max days",
    "min": "0",
    "description": "Days to retain ingested events. Set 0 to disable age expiry; the row limit still applies.",
    "kind": "int"
  },
  "mcp_call_log_args_capture": {
    "label": "MCP call log args capture",
    "description": "Applies to future events. Off drops tool arguments and results; Metadata retains argument keys and byte counts; Full retains values. Existing records are unchanged.",
    "options": [{ "value": "off", "label": "Off" }, { "value": "metadata", "label": "Metadata" }, { "value": "full", "label": "Full" }],
    "kind": "str"
  },
  "mcp_call_log_full_capture_tools": {
    "label": "MCP call log full capture tools",
    "description": "In Metadata mode, matching tools retain full arguments and results. Enter one tool name, glob, or regex per line. Off still drops values.",
    "kind": "list[str]"
  },
  "perceived_empty_probe_threshold": {
    "label": "Perceived empty probe threshold",
    "min": "2", "max": "25",
    "description": "Empty-result probes required to detect an episode, from 2 to 25.",
    "kind": "int"
  },
  "perceived_empty_window_seconds": {
    "label": "Perceived empty window seconds",
    "min": "10", "max": "3600",
    "description": "Window for counting empty-result probes, from 10 to 3600 seconds.",
    "kind": "int"
  },
  "status_bar_visibility": {
    "label": "Status bar visibility",
    "kind": "dict[str, bool]"
  },
  "architect_default_boot_nudge": {
    "label": "Architect default boot nudge",
    "kind": "str"
  },
  "engineer_default_boot_nudge": {
    "label": "Engineer default boot nudge",
    "kind": "str"
  },
  "relay_enabled": {
    "label": "Relay enabled",
    "kind": "bool"
  },
  "relay_url": {
    "label": "Relay url",
    "kind": "str"
  },
  "relay_daemon_id": {
    "label": "Relay daemon id",
    "kind": "str"
  },
  "relay_credential_id": {
    "label": "Relay credential id",
    "kind": "str"
  },
  "relay_private_key_path": {
    "label": "Relay private key path",
    "kind": "str"
  },
  "ai_enabled": {
    "label": "Ai enabled",
    "kind": "bool"
  },
  "ai_generation_provider": {
    "label": "Ai generation provider",
    "kind": "str"
  },
  "ai_anthropic_model": {
    "label": "Ai anthropic model",
    "kind": "str"
  },
  "ai_openai_compatible_base_url": {
    "label": "Ai openai compatible base url",
    "kind": "str"
  },
  "ai_openai_compatible_model": {
    "label": "Ai openai compatible model",
    "kind": "str"
  },
  "ai_embedding_model": {
    "label": "Ai embedding model",
    "kind": "str"
  },
  "ai_embedding_runtime": {
    "label": "Ai embedding runtime",
    "kind": "str"
  },
  "ai_index_corpus": {
    "label": "Ai index corpus",
    "kind": "dict[str, bool]"
  },
  "ai_boot_summary_enabled": {
    "label": "Ai boot summary enabled",
    "kind": "bool"
  },
  "ai_boot_summary_min_interval_seconds": {
    "min": "0",
    "label": "Ai boot summary min interval seconds",
    "kind": "int"
  },
  "ai_boot_summary_max_refreshes_per_hour": {
    "min": "0",
    "label": "Ai boot summary max refreshes per hour",
    "kind": "int"
  }
};
