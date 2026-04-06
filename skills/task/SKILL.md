---
description: Show details of a Loom task
allowed-tools: mcp__loom__weaver_task_show, mcp__loom__loom_context
argument-hint: [task slug or ID]
---

Show full details of a task.

If $ARGUMENTS is provided, use it as the task slug or ID and call weaver_task_show.

If $ARGUMENTS is empty, call loom_context to find the current agent's linked task, then call weaver_task_show with that task's slug.

Format the output clearly:
- Title and description
- Lane, labels, and action
- Assigned agent (if any)
- Pipeline info (parent task, depth) if it's a derived task
