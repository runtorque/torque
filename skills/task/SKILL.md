---
description: Show details of a Torque task
allowed-tools: mcp__torque__task_get, mcp__torque__context
argument-hint: [task slug or ID]
---

Show full details of a task.

If $ARGUMENTS is provided, use it as the task slug or ID and call task_get.

If $ARGUMENTS is empty, call context to find the current agent's linked task, then call task_get with that task's slug.

Format the output clearly:
- Title and description
- Lane, labels, and action
- Assigned agent (if any)
- Pipeline info (parent task, depth) if it's a derived task
