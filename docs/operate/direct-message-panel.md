# Direct-message panel
Torque shows a durable user↔agent conversation panel immediately below the
viewed terminal. The panel is per viewed agent: switching from an Architect to
an Engineer or Worker shows that agent's single normalized direct-message
thread.

## What appears there
- Agent messages sent with `architect_message_user`, `engineer_message_user`,
  or `torque_message_user`.
- User replies sent from the panel.
- Display mirrors of blocking asks and ask replies, marked with the blocking
  badge when the ask is still a decision gate.

Blocking ask cards still resolve through the existing ask-answer flow; a normal
panel reply is only a conversational message.

## Delivery behavior
User replies are persisted before delivery. Torque queues them into a live agent
without interrupting in-flight work and injects them as `## Message from the
User`. If the agent is down, dismissed, or temporarily unavailable, the row
stays buffered and replays on the next wake.

Agent→user direct messages can fire a macOS notification when group attention
notifications are enabled. User→agent replies do not notify. Notification
failure is non-fatal: the message remains persisted and visible in the panel.
The panel is fed by WebSocket snapshot/delta state and has no iTerm2 UI
dependency, so it is safe groundwork for future remote-browser access.
