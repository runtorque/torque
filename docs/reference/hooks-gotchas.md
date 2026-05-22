# Claude Code hooks gotchas

Implementation notes for Torque's Claude Code hook integration.

- **SessionStart only supports command hooks**: `SessionStart`, `WorktreeCreate`, and `WorktreeRemove` events only work with `type: "command"` hooks. HTTP hooks (`type: "http"`) are silently ignored for these events. Use a `curl`-based command hook instead.
- **allowedEnvVars required for HTTP headers**: HTTP hooks that interpolate env vars in headers (e.g., `"X-Torque-Cell-Id": "$TORQUE_CELL_ID"`) must list those vars in `allowedEnvVars`. Without it, the variable is not interpolated and the header is empty.
- **Hook config location**: Claude Code reads `.claude/settings.local.json` relative to the project root (git root). Torque writes hooks there using a merge strategy — Torque hooks are identified by their URL (`localhost:18932/events`) and can be added/removed without affecting user hooks.
- **Session ID**: Available in all hook payloads as the top-level `session_id` field. Persisted by Torque for `claude --resume` on relaunch. A `/clear` command generates a new session ID.
