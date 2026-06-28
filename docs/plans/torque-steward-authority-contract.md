# Torque Steward Wave A authority and foundation contract

Date: 2026-06-25  
Anchor: TORQUE:951 / TORQUE:953  
Decision anchor: `decision-8d0bd19608d5` — the Steward represents the user's wishes, may eventually receive broad user-delegated powers, but autonomous behavior must remain conservative and powerful actions need debate/confirmation/audit as appropriate.

## 1. Executive contract

Torque Steward is the built-in, Architect-derived group operations steward. It is **not** a silent god-Architect and is not a replacement for Blueprint/Product Manager, Torqly, or Creative/Catalyst.

Wave A defines and implements only a safe foundation:

- **Identity:** built-in draft Agent Class `torque-steward@1`, primary label **Torque Steward**, internal generated policy `class-policy-torque-steward@1`.
- **Authority:** read-only operational observation and recommendation only.
- **Autonomous behavior:** conservative summarize/explain/detect/recommend; no state mutation.
- **User-directed powerful behavior:** future reviewed waves only; explicit user requests are treated as requests to design/confirm/audit/hand off, not as current permission to execute.
- **Onboarding:** no auto-create, no auto-run, no surprise relaunch behavior in Wave A.
- **Review gate:** backend/scoping/security review is required before merge; later waves need their own review/security gates before any mutating authority.

## 2. Authority model

### 2.1 Behavior modes

| Mode | Trigger | Wave A allowed behavior | Wave A denied behavior | Required before later enablement |
|---|---|---|---|---|
| Passive/boot observation | Steward is launched or inspected | Read visible group context, summarize health, explain Torque concepts, list assumptions | Creating/moving/assigning/dispatching tasks; messaging agents; changing settings | Explicit UX/onboarding approval before auto-create or scheduled/background use |
| Conservative autonomous suggestion | Steward notices stale/stuck/missed-handoff risk while active | State evidence, confidence, recommended next step, authorized actor | Silent mutation; autonomous pings; autonomous cleanup; auto-routing | Event/subscription design, notification policy, throttling, audit log, opt-in settings |
| Explicit user-directed request | User asks Steward to do something powerful | Debate risk, ask clarifying/confirmation questions, name audit/rollback requirements, hand off to authorized actor/tool if current Steward lacks authority | Treating user request as implicit god-mode; executing unavailable powers through raw/freeform channels | Capability-specific tool design, confirmation UX, durable audit, rollback story, security review |
| Emergency/admin operation | Restart, compaction, deploy, release, profile mutation, scheduler/notification changes | Not allowed in Wave A; produce a runbook/checklist and authorized handoff | Performing action | Separate high-risk wave with preflight, confirmation, audit, rollback, and operator smoke requirements |

### 2.2 Authority matrix by capability area

| Area | Autonomous Wave A | Explicit user-directed Wave A | Later-wave path |
|---|---:|---:|---|
| Read self/group task context | Allow | Allow | Keep; expand only with scoping tests |
| Read recent events/MCP telemetry | Allow | Allow | Keep; add health heuristics carefully |
| Explain Torque concepts/onboarding | Allow | Allow | Integrate with Help once Help surface exists |
| Ops health / anomaly / stale/stuck detection | Allow as summary/recommendation | Allow as requested report | Add scheduled/event-driven checks only after opt-in notification design |
| Missed handoff detection | Allow as report | Allow as report | Add durable handoff detector and escalation rules later |
| Cleanup recommendations | Allow as proposal/checklist | Allow as checklist | Add cleanup actions only with confirmation/audit/rollback |
| User messages / asks | Deny in implemented Wave A class | Deny in implemented Wave A class; terminal/session output only | Add explicit user-message/ask powers after UX/noise policy review |
| Engineer/Worker messaging or control | Deny | Deny | Needs dedicated steward-to-agent communication policy, anti-spam guardrails, audit |
| Task create/update/move/assign/dispatch | Deny | Deny | Split queued proposals from executable mutation; require confirmation and route/audit gates |
| Engineer hire/roster management | Deny | Deny | Human-confirmed staffing wave only |
| Worktree checkpoint/merge/rebase/PR | Deny | Deny | Release/worktree-security wave with diff preflight and rollback |
| Restart/compaction/session repair | Deny | Deny | Runtime-admin wave with preflight, affected-agent visibility, operator confirmation, rollback/recovery notes |
| Notifications/scheduling | Deny | Deny | Opt-in notification/scheduler wave with rate limits and quiet hours |
| Profile/Class/role/specialization mutation | Deny | Deny | Prompt/config-admin wave with validation, preview diff, review approval, rollback |
| Deploy/release/admin settings | Deny | Deny | Release/admin wave; never silent; requires operator smoke/deploy evidence |
| Accepted decisions/product authority | Deny | Deny | Steward can recommend decisions; acceptance remains authorized product/architecture authority |
| External connectors | Not governed by Agent Class/Profile | Not governed by Agent Class/Profile | Connector governance must be designed separately before relying on class restrictions |

## 3. Representation contract

### 3.1 Built-in Agent Class identity

Implemented Wave A class:

- File: `torque/builtin_agent_classes/torque-steward.yaml`
- ID/version: `torque-steward@1`
- Primary label: **Torque Steward**
- Base runtime kind: `architect`
- Secondary label: `Architect-derived`
- Lifecycle: `draft`
- Draft marker: `draft.scratch_only: true`
- Generated internal profile: `class-policy-torque-steward@1`
- Archetype metadata: `torque_steward`
- Foundation metadata: `foundation_wave: A`, `authority_model: conservative_observer_suggester`, `auto_create_enabled: false`, `mutating_authority: none`, `user_delegated_power_surface: future_reviewed_waves_only`

The class is Architect-derived because the long-term Steward represents user wishes at group scope, but Wave A does **not** grant raw Architect authority.

### 3.2 Implemented capability ceiling

Allowed operator buckets:

- `self_context`
- `planning_reads`
- `recent_context_reads`
- `board_task_reads`

Compiled grants are read-only observation atoms:

- `observe.self_context`
- `observe.board_summary`
- `observe.task_detail`
- `observe.events`
- `observe.mcp_calls`
- `planning.area_read`
- `planning.initiative_read`
- `decision.list`
- `task.board_sync_read`

Explicit restrictions:

- `deny_engineer_management`
- `deny_worker_dispatch`
- `deny_execution_task_control`
- `deny_engineer_worker_messages`
- `deny_worktree_merge`
- `deny_deploy_admin`
- `deny_class_profile_admin`
- `deny_decision_acceptance`
- `deny_raw_tool_picker`
- `deny_high_risk_operations`

Additional validation blocks any `torque_steward` Wave A class from selecting capabilities outside the read-only operational-observation allowlist. This intentionally prevents accidental broadening before later reviewed waves.

### 3.3 Prompt posture

The prompt contract is:

- Represent the user's operational wishes, not autonomous agent ambition.
- Start with visible context and evidence.
- Separate observation, inference, risk, and recommendation.
- Recommend the smallest safe next step and name the authorized actor.
- Treat powerful user requests as future reviewed power-path requests unless a later approved tool/class grants the power.
- Never claim restart/compaction/notification/scheduling/task/engineer/worktree/deploy/profile/class/admin/accepted-decision powers in Wave A.

Implementation updates the restricted Architect prompt path to recognize `torque-steward` and use operations language instead of Product Manager / Creative language.

### 3.4 UI/status contract

Preview/frozen snapshots include `torque_steward_status`:

- `authority_model: conservative_observer_suggester`
- `foundation_wave: A`
- `represents_user_wishes: true`
- `auto_create_enabled: false`
- `raw_architect_authority: false`
- `autonomous_mutation_authority: false`
- `user_delegated_power_surface: future_reviewed_waves_only`
- false booleans for direct Engineer/Worker messaging, Worker dispatch, restart/compaction, notifications/scheduling, deploy/admin, profile/class admin, accepted-decision authority
- `confirmation_required_before_powerful_actions: true`
- external connector caveat

Recommended UI rendering in later UI wave:

- Badge: **Torque Steward** + `Draft`/`Read-only` chip.
- Secondary text: `Architect-derived · observation/recommendation only`.
- Warning banner: `No auto-create/auto-run or broad user-delegated powers in Wave A`.
- Advanced/Internal panel may show `class-policy-torque-steward@1`; normal compact UI should emphasize authority contract over raw atoms.

## 4. Onboarding and lifecycle plan

### Wave A implemented behavior

- The class exists as a built-in draft class for review/preview/explicit launch only.
- It is not assigned as a default class for any base kind.
- It is not auto-created for existing or new groups.
- It does not run on daemon startup/relaunch.
- It does not create background tasks, schedules, notifications, or messages.

### Later onboarding design requirements

Before auto-create or first-run offers:

1. **Explicit opt-in UX:** user sees what the Steward is, why it exists, and what it can/cannot do.
2. **No surprise agents:** existing groups should get an offer/empty-state card, not a silent running agent.
3. **First-run checklist:** explain Torque concepts, show current group health, ask what the user wants monitored.
4. **Relaunch behavior:** if the user assigned a Steward, relaunch must preserve desired/effective class snapshots; no hidden privilege escalation.
5. **Dismiss/disable path:** user can hide or disable Steward offer without affecting existing Architects/Engineers/Workers.
6. **Audit trail:** creation/assignment/auto-run settings must be visible in Agent Class audit or a dedicated Steward settings audit.

## 5. Responsibilities and routing boundaries

### Wave A responsibilities

- Group onboarding explanation: clarify groups, Architects, Engineers, Workers, tasks, decisions, actions, worktrees, and review gates using visible context.
- Ops health summary: stale tasks, stuck workers, long-running no-progress agents, failing review/fix loops, unresolved asks, unhealthy task aggregate states.
- Missed handoff awareness: parent task still waiting after child done/error, review required but not derived, artifact required but missing, verification promised but absent.
- Cleanup recommendations: orphaned/stale branches/worktrees, archived/done task cleanup candidates, noisy warnings, legacy direct profile assignments where visible.
- Safe escalation: recommend whether user, Torqly/implementation Architect, Blueprint/Product Manager, release/worktree specialist, runtime/quality worker, or future Steward power wave should act.

### Boundaries with existing built-ins

- **Blueprint/Product Manager:** owns product framing, task intake, proposed decisions. Steward may identify operational need but should not become product owner.
- **Torqly:** routes/implements engineering work. Steward may recommend routing but should not dispatch or manage Engineers/Workers in Wave A.
- **Creative/Catalyst:** ideation/exploration. Steward may identify where ideation would help but should not generate broad product strategy as its primary role.
- **Default Architect:** full scope/routing authority. Steward is narrower and must not imply default Architect powers.

## 6. Confirmation, audit, visibility, and rollback expectations

Any later Steward mutating power must define these before implementation:

1. **Actor and authority:** user-directed vs autonomous; exact capability atom/tool; allowed scopes; denied scopes.
2. **Preflight:** what the Steward reads/checks before proposing the action; conflict/staleness detection.
3. **Confirmation:** explicit user confirmation threshold; when confirmation can be remembered; how to show options/trade-offs.
4. **Audit:** durable record with user request, plan, confirmation, tool call, result, changed entities, and follow-up/rollback notes.
5. **Visibility:** UI/status surface that shows pending/recent Steward actions and why they happened.
6. **Rollback:** exact rollback or recovery path where possible; when rollback is impossible, state that before confirmation.
7. **Rate/noise limits:** especially for notifications, scheduling, and agent nudges.
8. **Review/security gates:** independent backend/scoping/security review, targeted tests, and live/deploy/smoke notes where applicable.

## 7. Follow-up implementation waves

Recommended next waves, in order:

1. **Wave B — UI/onboarding preview (non-mutating):** show Steward class in Agent Class picker with read-only/draft warnings; add optional group card/Help copy; no auto-create. Tests: frontend class rendering and no surprise default assignment.
2. **Wave C — Ops health read model (non-mutating):** implement a dedicated read-only health summary API/tool surface for Steward/Architect use, reusing existing health/details without mutation. Tests: scoping, hidden counts, stale/missed-handoff heuristics.
3. **Wave D — User-visible recommendation artifacts (low-risk write, gated):** allow Steward to attach/draft recommendations or user messages after explicit UX review. Tests: rate/noise gates, artifact/message audit.
4. **Wave E — Notifications/scheduling (mutating/interruptive):** opt-in schedules, quiet hours, throttling, durable notification audit, disable path.
5. **Wave F — Runtime maintenance proposals:** restart/compaction/session repair proposal and confirmation flow; no execution until preflight/audit/rollback is reviewed.
6. **Wave G — Task/engineer operations:** user-confirmed queued task proposals first, then narrowly scoped dispatch/assignment/hire only with audit and anti-surprise gates.
7. **Wave H — Profile/class/config mutation:** preview-diff, validation, review approval, rollback, and external connector caveat handling.
8. **Wave I — Deploy/release/admin:** highest-risk; requires release runbook integration, operator confirmation, deploy smoke evidence, and no worker-context deploy/stop/restart violations.

## 8. Wave A implementation evidence

Narrow foundation code included in this wave:

- `torque/builtin_agent_classes/torque-steward.yaml` — built-in draft class with read-only capability buckets, explicit restrictions, prompt addendum, and conservative metadata.
- `torque/agent_classes.py` — registers built-in class base kind/policy mode; adds Steward-specific read-only validation, warnings, and `torque_steward_status` preview/snapshot contract.
- `torque/architect.py` — recognizes Steward class in restricted Architect prompt shaping and uses operations/stewardship language instead of PM/Creative product language.
- `docs/reference/agent-classes.md` — documents built-in Steward class and stricter read-only validation contract.
- `docs/plans/torque-steward-authority-contract.md` — this self-contained contract artifact source.
- Tests: `tests/test_agent_classes.py` and `tests/test_architect_prompt.py` cover class registry, compiled read-only grants, denied high-risk categories, validation rejection for non-read Steward capabilities, preview status contract, warnings, and prompt posture.

Why this is non-mutating/safe:

- No default class changed; `default-architect`, `default-engineer`, and `default-worker` remain untouched.
- No auto-create, auto-run, schedule, notification, dispatch, hire, merge, deploy, restart, compaction, settings, class/profile mutation, or admin tool was added.
- The compiled Steward class grants only read-capability atoms.
- User messaging/asks, peer messaging, task proposal writes, journal writes, memory publication, Thinking writes, and Idea Brief writes are intentionally not granted.
- The class is lifecycle `draft` and `draft.scratch_only: true` with explicit warnings.
- Existing Agent Class/Profile enforcement remains the mechanism; this wave only adds a narrow class and prompt/status scaffolding.

## 9. Non-goals held

This wave did **not** implement:

- automatic Steward creation or first-run onboarding agent;
- background monitoring/event subscription;
- notifications, schedules, pings, or quiet-hour behavior;
- restart, compaction, session repair, deploy, release, or admin execution;
- task create/update/move/assign/dispatch;
- Engineer hiring/roster changes or Engineer/Worker messaging/control;
- worktree checkpoint/merge/rebase/PR operations;
- Agent Class/Profile/role/specialization mutation powers;
- accepted-decision authority;
- external connector governance;
- replacing Blueprint/Product Manager, Torqly, or Creative/Catalyst.

## 10. Open product/security questions for Blueprint/Torqly

1. What exact UI should offer Steward on new vs existing groups without surprise creation?
2. Should the first user-visible Steward message be terminal-only, a user message, or a persisted recommendation artifact?
3. Which health heuristics are product-approved vs too noisy for autonomous suggestions?
4. What is the minimum durable audit schema for future user-directed powerful actions?
5. How should external connector access be governed for Steward-like roles, since Agent Classes/Profiles do not enforce connectors today?
6. Which actor owns acceptance of Steward-generated recommendations: user, Blueprint/Product Manager, Torqly, or group Architect?
7. Can any low-risk user-message power be granted before the notification/rate-limit design exists, or should all writes remain denied until Wave D?

## 11. Recommended next wave

Proceed with **Wave B: UI/onboarding preview (non-mutating)** after backend/scoping/security review ships Wave A. Wave B should make the class understandable and non-surprising in the Agent Class UI and group onboarding surfaces, while preserving `auto_create_enabled: false` and no mutating powers.

## 12. Wave B operating brief addendum

Date: 2026-06-28  
Anchor: TORQUE:958 / TORQUE:964

Wave B keeps the Wave A authority ceiling and adds a deterministic read-only
operating-brief helper for Steward-style sessions:

- `architect_steward_operating_brief` is projected only when the effective
  policy grants the same read atoms already allowed to `torque-steward@1`:
  board/task summaries and detail, events, MCP-call telemetry, Areas,
  Initiatives, and Decisions.
- The helper returns structured `observed_facts`, `inferred_risks`,
  `suggested_next_steps`, and `responsible_agent_suggestions` sections, plus
  Help references for Torque concept explanations.
- It reports bounded read-only anomalies: blocked asks, stale handoffs, stale
  reviews, missed user-update candidates, dangling/unused workers, silent
  agents/workstreams, unhealthy tasks, and visible branch-boundary/merge gates.
- It does **not** route, dispatch, message, create/update/move/assign tasks,
  hire/dismiss, accept decisions, merge/rebase/checkpoint worktrees, deploy,
  restart, schedule, notify, edit classes/profiles, or otherwise mutate state.
- The prompt now tells Steward sessions to prefer the helper when visible and to
  keep user-facing output structured as observed facts vs inferred risks vs
  suggested next steps.

Wave B implementation evidence:

- `torque/steward_brief.py` — pure read-only brief/anomaly builder.
- `torque/mcp_architect.py` / `torque/mcp_tools_shared.py` — Architect MCP spec
  and dispatch for `architect_steward_operating_brief`.
- `torque/agent_profiles.py` — projection mapping requiring only the Steward's
  existing read atoms; Product Manager and other narrower profiles do not get
  the helper unless they carry the full read set.
- `torque/architect.py` and `torque/builtin_agent_classes/torque-steward.yaml`
  — prompt guidance for structured brief use without broadening authority.
- Tests: `tests/test_mcp_steward.py`, plus projection/prompt assertions in
  `tests/test_agent_profiles.py`, `tests/test_agent_classes.py`, and
  `tests/test_architect_prompt.py`.
