# What is Torque?

Torque is a **single-user project management powerhouse** built for the era where one person can ship software like a small team — by hiring AI agents instead of people.

You install Torque, point it at your repo, and you stop being the engineer typing into a single chat window. You become the operator of a small org chart: an Architect plans the next release, Engineers coordinate within their slice of the work, Workers go off and write the code, and Torque keeps the whole thing legible. Every conversation, every diff, every handoff is a structured task on a board you can scroll back through later.

Torque runs locally as a Python daemon paired with a web UI in a native desktop window or a browser. The agents are real PTY sessions running `claude`, `codex`, or whatever else you've configured. The daemon's job is to keep that team coordinated, on-task, and out of each other's way.

## The 60-second tour

This is the full Torque UI:

![Annotated full-screen grid: User column far left, Architect (Loomer) named in a blue cell, a row of Engineers labeled Panelsmith / user-group-zo / right-panel-li / there-s-an-is, then Workers below, then the focus pane.](../images/grid.png)

You're looking at three things at once:

1. **The team grid** (top). Each cell is an agent. They're laid out left-to-right by seniority — your Architect first, then their Engineers, then the Workers each Engineer is currently coordinating. The colored borders, status dots, and activity text encode live state: running, idle, waiting on input, errored.
2. **The focus pane** (bottom-left). When you click an agent, this pane shows its live activity: the last few messages, current path, which provider it's running, whether it has a worktree open. It's how you peek at one teammate without context-switching to their tab.
3. **The board / agent panel** (right). Either the task board (Backlog → In Progress → Done with all the rich cards) or the panel of whichever Engineer or Architect you're focused on (their journal, their event stream, their worklog).

That's the whole product on one screen. Everything else in these docs is detail about how each piece works.

## What Torque is for

Torque exists because **a solo developer with three AI subscriptions can outwork a five-person team if the coordination overhead doesn't kill them**. The coordination overhead absolutely kills them by default.

Without Torque you live in a forest of terminal tabs, each running an agent that knows nothing about the others. They merge over each other's work. They re-invent context you already gave a different agent. You become a human switchboard whose only job is to remember which tab is doing which thing. By Tuesday you've shipped less than you would have shipped alone.

With Torque, you give up the switchboard role. The Architect and Engineers do that for you. You decide what to build; the team decides how to coordinate building it.

## What Torque is *not*

A few clarifications, because Torque sits next to several adjacent tools:

- **Not a multi-user platform.** Torque is single-operator on purpose. There's one human in the loop, one local daemon, one machine. The "team" is your fleet of agents, not your colleagues.
- **Not a CI/CD system.** Torque dispatches work and tracks it. Your agents still run your tests, your build, your deploy. Torque watches and records, it doesn't replace your pipeline runner.
- **Not a chat client.** You don't type at agents directly through Torque most of the time. You set up the work, the agents do it, and they report back through structured tasks. The terminal is still right there if you want to drop into a tab and steer.
- **Not provider-locked.** The current adapters cover Claude Code, Codex, and Gemini CLI. The shape of the system — terminal session + structured reporting protocol — is provider-agnostic.

## Where to next

If you want the full story of why each piece of the team exists — Workers first, then Engineers, then Architects — read [Why Torque exists](why-torque-exists.md). It walks through the problems that forced each abstraction into existence.

If you'd rather just install it and start, jump to [Getting started](getting-started.md).

If you want the vocabulary primer first, [Core concepts](core-concepts.md) is a one-page glossary you can keep in another tab while you read.
