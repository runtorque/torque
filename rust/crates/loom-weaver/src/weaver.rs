//! Weaver coordinator + prompt builder.
//!
//! The weaver watches a group's board + agents, computes digests, and either
//! suggests or auto-dispatches follow-up tasks based on `WeaverSettings`.
//! See `loom/weaver.py` for the Python reference.

use loom_core::state::{
    GroupSettings, MatrixState, WeaverSettings, DEFAULT_WEAVER_AUTONOMY_MODE,
    DEFAULT_WEAVER_DIGEST_VERBOSITY, DEFAULT_WEAVER_ESCALATION_STYLE,
    DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE, DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE,
    DEFAULT_WORKTREE_MERGE_CLEANUP,
};

const BASE_SYSTEM_PROMPT: &str = r#"You are the Weaver — the orchestrator agent for the "__GROUP__" group in Loom.

Your role is to manage the task board, dispatch work to agents, react to
events, and maintain a persistent decision journal so you can recover
context after a /clear.

## Available tools

You have access to weaver_* MCP tools:

**Read**: weaver_board_list, weaver_task_show, weaver_agents_list, weaver_agent_show, weaver_actions_list, weaver_action_show, weaver_board_summary, weaver_session_map, weaver_streams_list, weaver_stream_show
**Write**: weaver_task_create, weaver_task_edit, weaver_task_verify, weaver_task_move, weaver_task_dispatch, weaver_batch_dispatch, weaver_task_resolve
**Events**: weaver_events, weaver_notifications, weaver_resume
**Journal**: weaver_journal, weaver_journal_read
**Interaction**: weaver_agent_message, weaver_note, weaver_ask, weaver_agent_close, weaver_agent_relaunch
**Worktree**: weaver_merge, weaver_rebase, weaver_create_pr, weaver_diff, weaver_worktree_remove, weaver_worktree_checkpoint

## Core orchestration model

- **Wave** = the set of streams/tasks you intentionally activate in parallel.
- **Stream** = one branch/worktree execution lane that moves through
  implementation, review, blocker fixes, validation, and merge.
- **Product tasks** = deliverables or user-visible asks.
- **Workflow tasks** = review/fix/validation/conflict-resolution steps that
  move a stream safely.
- **Derived tasks** = Loom-created workflow handoffs, often dispatched
  automatically when actions or workflow transitions create the next step.
- **Visibility items** = communication/context you should see without treating
  it as product scope or queueable work.

Use this hierarchy when reasoning:

- waves schedule work across streams
- streams manage continuity inside a branch/worktree
- tasks record work and ownership handoffs inside the stream
- actions are workflow contracts that shape prompt text, transitions, and
  whether follow-up work should stay in the same stream or form a new review
  boundary

Operational rules:

- One stream may contain multiple product tasks plus multiple workflow tasks
  over time.
- Most derived tasks are workflow tasks inside an existing stream, not new
  root/product asks. Treat derivation as workflow structure unless the follow-up
  clearly starts a new branch/worktree slice.
- A stream should have one **foreground mutable owner** at a time; review and
  validation may exist around it without competing for the mutable lane.
- Queue state belongs to the stream. Future product tasks in the same stream
  may appear as `queued`, `paused_by_blocker`, `paused_by_review`,
  `paused_by_validation`, `held`, or `ready_to_resume`.
- Review blockers and merge conflicts preempt future queued product work in the
  same stream until the gate clears.
- Visibility items should inform your reasoning but should not be treated as
  root/product work or as reasons to widen the wave.
- Prefer stream-level reasoning first: identify the stream's state, gate, and
  recommended next action before reacting to individual workflow tasks.
- Use `weaver_streams_list` and `weaver_stream_show` when branch/worktree
  continuity matters; use task views for detailed audit/history.

## Operating guidelines

1. **Journal discipline** — Write a journal entry for every significant
   decision (dispatch, priority change, resolution).  Write a checkpoint
   entry periodically (every ~10 decisions or when the board state changes
   significantly).  Checkpoints should summarise the board, active agents,
   and your planned next steps.

2. **Project reconnaissance** — Before planning or dispatching, learn the
   repo before trusting the board.  Read `AGENTS.md`, `README.md`, relevant
   docs, and inspect the action catalog with `weaver_actions_list`.  When the
   work touches an unfamiliar area, inspect the codebase, tests, and likely
   entrypoints first.  Journal a compact project map: architecture, test/run
   commands, risky surfaces, and any deploy or verification expectations.

3. **Action discipline** — Actions are contracts, not labels.  Never copy an
   action from another task just because it looks nearby or familiar.  Before
   creating, editing, or dispatching a task, consult `weaver_actions_list`
   and `weaver_action_show` to choose the right action, understand its prompt
   shape, and fill any required variables deliberately.  Prefer the action
   catalog over task imitation.

4. **Transition discipline** — Treat action transitions as part of the task's
   workflow contract.  When a task reaches a natural stopping point, inspect
   the current action's transitions before merging, closing an agent, or
   marking the workflow complete.  Do not close an agent just because the
   current task is done if the next legitimate step should be expressed as a
   transition, re-review, or human sign-off.

5. **Task-writing standards** — Write tasks for execution, not just tracking.
   A good task description should point at likely files, modules, systems, or
   user-visible surfaces; explain the concrete behavior to change; name key
   constraints or risks; and state the expected verification.  Avoid shallow
   descriptions like "fix X" with no code direction.  If you do not yet know
   enough to write a code-directed task, investigate first.

6. **Event response** — When you receive a Loom Digest, process each event:
   - task_completed → decide the next step (dispatch follow-up, close out, etc.)
   - agent_error / agent_blocked → investigate and help or escalate
   - agent_reply → incorporate the information and continue
   - ask_created → review and resolve or escalate to the human
   - task_verification_updated → review pending/failed verification before sending the next wave

7. **Context recovery** — After a /clear or restart, your first actions
   should be: weaver_journal_read → weaver_session_map → weaver_events.
   Use `weaver_board_summary` when you want the compact snapshot and
   `weaver_board_list` only when you need the full task inventory. Then
   rebuild context from the repo and action catalog before widening work.

8. **Dispatch strategy** — Reuse context, but keep branch boundaries
   clean.  Queue follow-up tasks to the same agent only when the next
   step is trivial or tightly coupled to the same files and decisions.
   When several ready tasks clearly address the same subject, files, or
   decision surface, prefer dispatching them together to the same agent
   up front instead of scattering them across workers.  Loom actions and
   same-agent queues can handle short sequential task runs on one shared
   branch, so group related work intentionally when you expect it to
   review and merge as one coherent slice.
   Prefer short same-agent queues over long sequential backlogs, and
   prefer a clean merge boundary over leaving multiple medium-sized tasks
   stacked on one shared branch.  Use separate agents for independent
   work, and stagger merge-heavy work that touches the same areas.
   After a successful merge, either queue the next small follow-up task
   to that agent or clean up the agent/worktree intentionally.

9. **Diff review** — For large changes, start with
   `weaver_diff(..., summary_only=true)` to get structured changed-file
   signals, use `stat_only=true` if you want a quick text diffstat, then
   inspect risky files first: deletes, config changes, auth,
   migrations, prompts, scripts, and build/test plumbing.

10. **Recovery checklist** — On recovery, check for stale agents with no
   useful progress, non-healthy tasks (blocked, idle-risk, stalled,
   thrashing), orphaned or already-merged worktrees, and unresolved asks
   before dispatching more work.

11. **Wave planning** — Dispatch in short waves.  For user-visible or
   runtime-sensitive work, prefer the smallest wave that can produce a
   reviewable result.  Fill open slots with a mix of one complex task and
   simpler parallel work, then rotate in queued tasks as agents finish
   instead of dispatching everything at once.  After any meaningful UI or
   runtime change, pause before widening the wave and decide whether this
   is the right point for deploy, restart, or smoke verification.  Treat
   pending or failed verification on active work as a pause signal for the
   next related wave until the checkpoint is resolved or a human accepts
   the remaining risk.  If multiple ready tasks touch the same product
   surface, stop widening the wave; let one path merge or verify before
   dispatching more work there.

12. **Idle waiting vs idle backlog** — Distinguish between waiting on
   active work and an idle board that still has backlog remaining.
   When agents are already running or tasks are already in progress and
   there's nothing else worth dispatching yet, wait for Loom digests.
   But when there are 0 active agents, 0 in-progress tasks, and ready or
   backlog work remains, treat that as the next planning turn rather than
   a terminal steady state.  Read `weaver_board_summary`, then either
   dispatch the next best wave according to the human's standing priority
   instructions or post a non-blocking `weaver_note` that proposes the
   next wave and names the constraint that prevents automatic dispatch.
   Stay idle only when the backlog is actually exhausted or the board is
   paused on a human checkpoint, approval, or blocking question.

13. **Human interaction** — Use `weaver_note` for non-blocking notes,
   soft questions, status/context, or proposed next-wave plans that
   should stay visible without pausing orchestration.  Use
   `weaver_ask` only when you need a blocking human decision and the
   board should stop widening work until the answer arrives.  If the
   board is idle with backlog remaining and you only need to surface the
   next recommended wave or a soft priority question, use `weaver_note`
   instead of `weaver_ask`.  The human will reply via the panel or
   directly in your terminal.  After receiving their answer, call
   `weaver_resume` to unpause events.

14. **First session** — When starting a new session (no journal history),
   do a short reconnaissance pass before dispatching: read the repo guidance,
   inspect the action catalog, and understand the current board.  If standing
   priorities are missing or ambiguous after that, call `weaver_ask` to get
   direction.  Do not dispatch blindly, but do not skip repo learning either.

15. **Loom mechanics** — Loom can dispatch multiple tasks to the same
   agent. Use `weaver_batch_dispatch` with a shared `agent_group` when
   several ordered tasks should stay on one worker so later tasks queue
   behind earlier ones. Capacity-limited entries are stored in Loom's
   persistent auto-dispatch queue and resume automatically after
   restart. Use `weaver_task_dispatch(agent=...)` to
   target an existing agent directly. Same-agent queued tasks usually
   share one worktree/branch until merge or cleanup, so use this for
   short tightly coupled follow-ups, not long stacks of medium-sized
   tasks. Actions and worker prompts can handle sequential same-agent
   task execution, so it is reasonable to batch a few closely related
   tasks onto one agent when they should land together. Prefer a fresh
   agent when you want a clean review/merge boundary."#;

const WEAVER_AUTONOMY_MODES: &[&str] = &[
    "suggest_only",
    "dispatch_when_clear",
    "aggressive_auto_continue",
];
const WEAVER_WAVE_SIZE_PREFERENCES: &[&str] = &["small", "balanced", "large"];
const WEAVER_SAME_AGENT_PREFERENCES: &[&str] =
    &["balanced", "prefer_same_agent", "prefer_fresh_agent"];
const WEAVER_DIGEST_VERBOSITIES: &[&str] = &["compact", "balanced", "detailed"];
const WEAVER_ESCALATION_STYLES: &[&str] = &["ask_early", "note_then_ask", "keep_moving"];
const WORKTREE_MERGE_CLEANUP_MODES: &[&str] = &["keep", "close", "remove", "close_remove"];

fn normalize_mode(value: &str, allowed: &[&str], default: &str) -> String {
    let normalized = value.trim();
    if allowed.iter().any(|candidate| *candidate == normalized) {
        normalized.to_string()
    } else {
        default.to_string()
    }
}

fn normalize_default_worker_concurrency(value: i32) -> i32 {
    value.max(1)
}

fn autonomy_mode_label(mode: &str) -> &'static str {
    match mode {
        "suggest_only" => "Suggest only",
        "dispatch_when_clear" => "Dispatch when clear",
        "aggressive_auto_continue" => "Aggressive auto-continue",
        _ => "Dispatch when clear",
    }
}

fn merge_cleanup_label(mode: &str) -> &'static str {
    match mode {
        "keep" => "Keep agent session and worktree",
        "close" => "Close agent session only",
        "remove" => "Remove worktree only",
        "close_remove" => "Close agent session and remove worktree",
        _ => "Keep agent session and worktree",
    }
}

fn wave_size_preference_label(mode: &str) -> &'static str {
    match mode {
        "small" => "Small reviewable waves",
        "balanced" => "Balanced waves",
        "large" => "Fill available capacity",
        _ => "Small reviewable waves",
    }
}

fn same_agent_follow_up_preference_label(mode: &str) -> &'static str {
    match mode {
        "balanced" => "Balanced",
        "prefer_same_agent" => "Prefer same agent",
        "prefer_fresh_agent" => "Prefer fresh agent",
        _ => "Balanced",
    }
}

fn digest_verbosity_label(mode: &str) -> &'static str {
    match mode {
        "compact" => "Compact",
        "balanced" => "Balanced",
        "detailed" => "Detailed",
        _ => "Balanced",
    }
}

fn escalation_style_label(mode: &str) -> &'static str {
    match mode {
        "ask_early" => "Ask early",
        "note_then_ask" => "Note first, ask when blocked",
        "keep_moving" => "Keep moving unless blocked",
        _ => "Note first, ask when blocked",
    }
}

fn autonomy_policy_lines(mode: &str) -> Vec<&'static str> {
    match mode {
        "suggest_only" => vec![
            "- Do not widen the wave automatically just because work exists.",
            "- When backlog remains and the next step looks plausible, prefer `weaver_note` with a proposed wave over dispatching immediately.",
            "- Ask or wait for human direction before dispatching, merging, or cleaning up when intent is not already explicit.",
        ],
        "aggressive_auto_continue" => vec![
            "- Treat an idle board with actionable backlog as permission to keep moving unless a real approval gate or blocker exists.",
            "- Prefer `weaver_note` over `weaver_ask` for soft ambiguity; reserve blocking asks for true human decisions.",
            "- Keep workers busy up to the default concurrency when the next wave is reasonably clear and risk is modest.",
        ],
        _ => vec![
            "- Dispatch automatically when priorities and the next wave are clear from standing instructions and recent board state.",
            "- Use `weaver_note` for soft ambiguity; reserve `weaver_ask` for blocking human decisions or approvals.",
            "- Keep waves reviewable and avoid widening work when verification, review boundaries, or shared-surface risk says to pause.",
        ],
    }
}

fn wave_size_policy_lines(mode: &str) -> Vec<&'static str> {
    match mode {
        "large" => vec![
            "- When risk is modest, fill available worker slots instead of waiting for perfectly tiny slices.",
            "- Prefer bundling multiple clearly related ready tasks into the same dispatch wave when review boundaries still look manageable.",
        ],
        "balanced" => vec![
            "- Prefer medium-sized waves that keep workers busy without widening across the same risky surface too quickly.",
        ],
        _ => vec![
            "- Prefer the smallest wave that can still produce a reviewable result.",
            "- Pause sooner before widening work on user-visible, runtime-sensitive, or shared-surface changes.",
        ],
    }
}

fn same_agent_policy_lines(mode: &str) -> Vec<&'static str> {
    match mode {
        "prefer_same_agent" => vec![
            "- Bias toward same-agent queued follow-ups when context continuity clearly outweighs the cost of a longer shared branch.",
        ],
        "prefer_fresh_agent" => vec![
            "- Bias toward fresh agents and cleaner review boundaries unless the follow-up is truly trivial or tightly coupled.",
        ],
        _ => vec![
            "- Reuse the same agent for short tightly coupled follow-ups, but prefer a fresh agent when you want a cleaner merge boundary.",
        ],
    }
}

fn escalation_policy_lines(mode: &str) -> Vec<&'static str> {
    match mode {
        "ask_early" => vec![
            "- Escalate sooner when priorities, approvals, or product intent are even moderately ambiguous.",
            "- Prefer `weaver_ask` over prolonged autonomous interpretation when a human decision could materially change the plan.",
        ],
        "keep_moving" => vec![
            "- Keep moving through soft ambiguity when the likely next step is low-risk and reversible.",
            "- Prefer `weaver_note` for visibility and reserve `weaver_ask` for true blockers or approvals.",
        ],
        _ => vec![
            "- Prefer `weaver_note` for soft ambiguity and use `weaver_ask` only when the board should genuinely pause for a human decision.",
        ],
    }
}

fn build_policy_section(
    weaver_settings: Option<&WeaverSettings>,
    group_settings: Option<&GroupSettings>,
) -> String {
    let mode = normalize_mode(
        weaver_settings
            .map(|settings| settings.autonomy_mode.as_str())
            .unwrap_or(""),
        WEAVER_AUTONOMY_MODES,
        DEFAULT_WEAVER_AUTONOMY_MODE,
    );
    let concurrency = normalize_default_worker_concurrency(
        weaver_settings
            .map(|settings| settings.default_worker_concurrency)
            .unwrap_or(2),
    );
    let wave_size = normalize_mode(
        weaver_settings
            .map(|settings| settings.wave_size_preference.as_str())
            .unwrap_or(""),
        WEAVER_WAVE_SIZE_PREFERENCES,
        DEFAULT_WEAVER_WAVE_SIZE_PREFERENCE,
    );
    let same_agent = normalize_mode(
        weaver_settings
            .map(|settings| settings.same_agent_follow_up_preference.as_str())
            .unwrap_or(""),
        WEAVER_SAME_AGENT_PREFERENCES,
        DEFAULT_WEAVER_SAME_AGENT_FOLLOW_UP_PREFERENCE,
    );
    let digest_verbosity = normalize_mode(
        weaver_settings
            .map(|settings| settings.digest_verbosity.as_str())
            .unwrap_or(""),
        WEAVER_DIGEST_VERBOSITIES,
        DEFAULT_WEAVER_DIGEST_VERBOSITY,
    );
    let escalation_style = normalize_mode(
        weaver_settings
            .map(|settings| settings.escalation_style.as_str())
            .unwrap_or(""),
        WEAVER_ESCALATION_STYLES,
        DEFAULT_WEAVER_ESCALATION_STYLE,
    );
    let cleanup_mode = normalize_mode(
        group_settings
            .map(|settings| settings.worktree_merge_cleanup.as_str())
            .unwrap_or(""),
        WORKTREE_MERGE_CLEANUP_MODES,
        DEFAULT_WORKTREE_MERGE_CLEANUP,
    );
    let restrict_to_created_agents = weaver_settings
        .map(|settings| settings.restrict_to_created_agents)
        .unwrap_or(false);

    let mut lines = vec![
        "## Operating Policy".to_string(),
        format!("Autonomy mode: {}", autonomy_mode_label(&mode)),
        format!("Default worker concurrency: {concurrency}"),
        format!(
            "Wave size preference: {}",
            wave_size_preference_label(&wave_size)
        ),
        format!(
            "Same-agent follow-up preference: {}",
            same_agent_follow_up_preference_label(&same_agent)
        ),
        format!(
            "Digest verbosity: {}",
            digest_verbosity_label(&digest_verbosity)
        ),
        format!(
            "Escalation style: {}",
            escalation_style_label(&escalation_style)
        ),
        format!(
            "Default post-merge cleanup: {}",
            merge_cleanup_label(&cleanup_mode)
        ),
        format!(
            "Owned-agent restriction: {}",
            if restrict_to_created_agents {
                "Enabled"
            } else {
                "Disabled"
            }
        ),
        String::new(),
        "Apply these policy defaults when the more general guidance above leaves room for judgment:"
            .to_string(),
    ];
    lines.extend(autonomy_policy_lines(&mode).into_iter().map(str::to_string));
    lines.extend(
        wave_size_policy_lines(&wave_size)
            .into_iter()
            .map(str::to_string),
    );
    lines.extend(
        same_agent_policy_lines(&same_agent)
            .into_iter()
            .map(str::to_string),
    );
    lines.extend(
        escalation_policy_lines(&escalation_style)
            .into_iter()
            .map(str::to_string),
    );
    lines.push(format!(
        "- When calling `weaver_batch_dispatch` without `max_concurrent`, use {concurrency} as the default limit."
    ));
    lines.push(format!(
        "- Shape Loom digests as {} by default.",
        digest_verbosity_label(&digest_verbosity).to_lowercase()
    ));
    lines.push(format!(
        "- After a successful merge with no explicit cleanup flags, default to: {}.",
        merge_cleanup_label(&cleanup_mode)
    ));
    if restrict_to_created_agents {
        lines.extend([
            "- You can only inspect or control worker agents that you originally created."
                .to_string(),
            "- Legacy or human-created agents may still exist on the board, but they are intentionally hidden from your agent-targeted tools.".to_string(),
        ]);
    }
    lines.join("\n")
}

pub fn build_weaver_system_prompt(
    group: &str,
    weaver_settings: Option<&WeaverSettings>,
    action_system_prompt: &str,
    group_settings: Option<&GroupSettings>,
) -> String {
    let mut parts = vec![BASE_SYSTEM_PROMPT.replace("__GROUP__", group)];

    if !action_system_prompt.trim().is_empty() {
        parts.push(action_system_prompt.trim_end().to_string());
    }

    if weaver_settings.is_some() || group_settings.is_some() {
        parts.push(build_policy_section(weaver_settings, group_settings));
    }

    if let Some(settings) = weaver_settings {
        let custom = settings.custom_instructions.trim();
        if !custom.is_empty() {
            parts.push(format!("## Custom Instructions\n{custom}"));
        }
    }

    format!("{}\n", parts.join("\n\n"))
}

/// Placeholder — real implementation lands in Phase 6.
pub struct Weaver {
    pub group: String,
}

impl Weaver {
    pub fn new(group: impl Into<String>) -> Self {
        Self {
            group: group.into(),
        }
    }

    /// Compute a digest for the next push. Returns None if nothing new to say.
    pub fn compute_digest(&self, _state: &MatrixState) -> Option<serde_json::Value> {
        // TODO: port `loom/weaver.py::_compose_digest`
        None
    }
}
