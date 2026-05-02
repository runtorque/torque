---
description: Show current Torque agent context, linked task, and pipeline state
allowed-tools: mcp__torque__torque_context
---

Show the current Torque agent context using the torque_context MCP tool.

Format the output clearly:
- Agent name, group, and status
- Current task title, lane, and action
- Pipeline info (parent task, depth) if this is a derived task
- Worktree branch and diff stats if in a worktree
