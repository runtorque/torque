# Schedules

Loom can create tasks on a timer and optionally dispatch them automatically. There are two related mechanisms:

- **Scheduled tasks**: a board task with `scheduled_at`
- **Schedules**: reusable one-shot or recurring rules stored in Loom

## Scheduled Tasks

You can create a normal task now and ask Loom to dispatch it later:

```bash
loom task create "Re-run weekly dependency audit" \
  -g backend \
  --at "tomorrow 09:00"
```

Supported `--at` formats include:

- ISO 8601 timestamps
- Relative values like `+30m`, `+2h`, `+1d`
- `tomorrow 09:00`

When the scheduled time arrives, Loom dispatches the task and clears the stored schedule time.

## Reusable Schedules

Schedules are first-class records for recurring or one-shot automation.

Create a recurring schedule:

```bash
loom schedule create nightly-tests \
  -g backend \
  --task "Run nightly test sweep" \
  --cron "0 2 * * *" \
  -t oneshot/fix
```

Create a one-shot schedule:

```bash
loom schedule create triage-followup \
  -g backend \
  --task "Follow up on auth rollout" \
  --at "+2h"
```

## What a Schedule Stores

- Name and slug
- Task title template
- Optional task description
- Group
- Optional action and action variables
- Optional labels
- Trigger type: cron or one-shot time
- Timezone
- Enabled/disabled state
- Run history metadata

Task title templates can include time placeholders such as `{date}`, `{time}`, and `{datetime}`.

## Managing Schedules

```bash
loom schedule list
loom schedule show nightly-tests
loom schedule edit nightly-tests --cron "0 3 * * *"
loom schedule disable nightly-tests
loom schedule enable nightly-tests
loom schedule run nightly-tests
loom schedule delete nightly-tests
```

## How Execution Works

The daemon periodically checks:

- board tasks with `scheduled_at`
- due schedule records

When a schedule fires, Loom creates a new board task and, if configured, dispatches it through the normal task flow.

## When to Use Which

- Use **scheduled tasks** when the timing is specific to one concrete task already on the board.
- Use **schedules** when you want a reusable automation rule that keeps creating new work over time.

## Related Docs

- [Task Board](board.md)
- [Actions & Templates](actions.md)
- [CLI Reference](cli.md#schedule)
