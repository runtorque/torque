---
description: Create a task and dispatch it to an agent
allowed-tools: mcp__torque__task_create, mcp__torque__task_dispatch, mcp__torque__task_list, mcp__torque__agent_list
argument-hint: <task description>
---

Create a new task and dispatch it to an agent.

If $ARGUMENTS is provided, use it as the task title.
Otherwise, ask the user what they'd like to dispatch.

Steps:
1. Create the task using task_create with the title
2. Dispatch it using task_dispatch with the new task's slug
3. Report which agent picked it up
