---
name: loom-status
description: Show current Loom agent context, linked task, and pipeline state
allowed-tools: mcp__loom__loom_context
---

Show the current Loom agent context using the loom_context MCP tool.

Format the output clearly:
- Agent name, group, and status
- Current task title, lane, and action
- Pipeline info (parent task, depth) if this is a derived task
- Worktree branch and diff stats if in a worktree
