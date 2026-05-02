---
description: Show details of a Torque task
allowed-tools: mcp__torque__engineer_task_show, mcp__torque__torque_context
argument-hint: [task slug or ID]
---

Show full details of a task.

If $ARGUMENTS is provided, use it as the task slug or ID and call engineer_task_show.

If $ARGUMENTS is empty, call torque_context to find the current agent's linked task, then call engineer_task_show with that task's slug.

Format the output clearly:
- Title and description
- Lane, labels, and action
- Assigned agent (if any)
- Pipeline info (parent task, depth) if it's a derived task
