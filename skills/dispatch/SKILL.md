---
description: Create a task and dispatch it to an agent
allowed-tools: mcp__torque__engineer_task_create, mcp__torque__engineer_task_dispatch, mcp__torque__engineer_board_list, mcp__torque__engineer_agents_list
argument-hint: <task description>
---

Create a new task and dispatch it to an agent.

If $ARGUMENTS is provided, use it as the task title.
Otherwise, ask the user what they'd like to dispatch.

Steps:
1. Create the task using engineer_task_create with the title
2. Dispatch it using engineer_task_dispatch with the new task's slug
3. Report which agent picked it up
