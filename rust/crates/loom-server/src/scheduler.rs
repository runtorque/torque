//! Cron-style scheduler — fires board tasks when their `scheduled_at` time
//! arrives.
//!
//! Background task that wakes every 15s, scans schedules + tasks, fires what
//! needs firing. Minimal v1 — no cron_expr support yet (only `scheduled_at`
//! one-shots).
//!
//! Also runs a periodic weaver-buffer drain so queued events don't stall
//! when no hook / PTY / command event is around to trigger a flush.

use std::sync::Arc;
use std::time::Duration;

use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::MatrixState;
use loom_worktree::{checkpoint::ahead_behind, diff::diff_vs_base};

use crate::app::AppState;

const TICK: Duration = Duration::from_secs(15);
const WEAVER_FLUSH_TICK: Duration = Duration::from_secs(5);
const WORKTREE_REFRESH_TICK: Duration = Duration::from_secs(60);

pub fn spawn(state: Arc<Mutex<MatrixState>>, db: LoomDb, bus: EventBus) {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(TICK);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            ticker.tick().await;
            tick(&state, &db, &bus).await;
        }
    });
}

/// Periodic weaver buffer drain. Runs every 5s; iterates groups with
/// queued events and attempts a flush. `flush_group` gates internally on
/// weaver status / pause / activity, so this is a no-op for groups whose
/// weaver isn't ready to receive.
///
/// Why separate from `tick`: that one needs a `MatrixState` + `LoomDb` +
/// `EventBus` only (schedule-firing is lightweight). Weaver flushing needs
/// the full `AppState` so it can hand `send_text_to_cell_quiet` the PTY /
/// terminal-bridge it might need.
pub fn spawn_weaver_flusher(app: AppState) {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(WEAVER_FLUSH_TICK);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            ticker.tick().await;
            app.weaver_buffer.maybe_flush_due_for_app(&app).await;
        }
    });
}

/// Periodic worktree status refresher. Mirrors Python's
/// `_worktree_diff_updater` — every 60s, for each cell with an active
/// worktree, recompute ahead/behind/dirty/diff stats and emit a delta when
/// anything changed. Without this, the agent cards never show the `Changes`
/// / `Checkpoints` / `↑N` rows that the Python UI relies on.
pub fn spawn_worktree_refresher(app: AppState) {
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(WORKTREE_REFRESH_TICK);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        // First tick fires immediately; skip it so startup isn't slowed by a
        // sweep of every cell's git state.
        ticker.tick().await;
        loop {
            ticker.tick().await;
            refresh_worktrees(&app).await;
        }
    });
}

async fn refresh_worktrees(app: &AppState) {
    // Snapshot the cells with active worktrees — we don't want to hold the
    // state lock while shelling out to git. We include cells with an empty
    // `worktree_base_branch` so we can backfill from git on legacy cells
    // whose base wasn't persisted at creation time.
    let jobs: Vec<(String, String, String)> = {
        let st = app.state.lock().await;
        st.agents
            .values()
            .filter(|cell| !cell.worktree_path.is_empty())
            .map(|cell| {
                (
                    cell.id.clone(),
                    cell.worktree_path.clone(),
                    cell.worktree_base_branch.clone(),
                )
            })
            .collect()
    };
    if jobs.is_empty() {
        return;
    }

    for (agent_id, worktree_path, mut base_branch) in jobs {
        if base_branch.is_empty() {
            let path = std::path::PathBuf::from(&worktree_path);
            if let Some(resolved) = resolve_fork_point(&path).await {
                base_branch = resolved;
                let mut st = app.state.lock().await;
                if let Some(cell) = st.agents.get_mut(&agent_id) {
                    cell.worktree_base_branch = base_branch.clone();
                    st.emit_agent(&agent_id);
                }
                if let Some(cell) = {
                    let st = app.state.lock().await;
                    st.agents.get(&agent_id).cloned()
                } {
                    let _ = app.db.save_agent(&cell).await;
                }
            } else {
                continue;
            }
        }
        // Three cheap git calls per cell. Swallow errors — a stale cell
        // (worktree deleted out from under us) should just leave the old
        // stats in place until the next create/remove resolves it.
        let path_owned = std::path::PathBuf::from(&worktree_path);
        let ahead_behind_path = path_owned.clone();
        let ahead_behind_base = base_branch.clone();
        let diff_path = path_owned.clone();
        let diff_base = base_branch.clone();

        let (ahead, behind) = ahead_behind(&ahead_behind_path, &ahead_behind_base)
            .await
            .unwrap_or((0, 0));
        let dirty = has_uncommitted(&worktree_path).await;
        let summary = tokio::task::spawn_blocking(move || diff_vs_base(&diff_path, &diff_base))
            .await
            .ok()
            .and_then(|res| res.ok());
        let summary = match summary {
            Some(s) => s,
            None => continue,
        };

        let mut diff_obj = serde_json::Map::new();
        diff_obj.insert("files".into(), summary.files.into());
        diff_obj.insert("insertions".into(), summary.insertions.into());
        diff_obj.insert("deletions".into(), summary.deletions.into());

        let mut changed_files = summary.changed_files.clone();
        changed_files.sort();
        changed_files.dedup();

        let changed = {
            let mut st = app.state.lock().await;
            let Some(cell) = st.agents.get_mut(&agent_id) else {
                continue;
            };
            let mut mutated = false;
            if cell.worktree_diff != diff_obj {
                cell.worktree_diff = diff_obj;
                mutated = true;
            }
            if cell.worktree_changed_files != changed_files {
                cell.worktree_changed_files = changed_files;
                mutated = true;
            }
            if cell.worktree_dirty != dirty {
                cell.worktree_dirty = dirty;
                mutated = true;
            }
            // Python maps "commits ahead" → `worktree_checkpoints` for the UI
            // badge, keeping `worktree_ahead` as the explicit field. We copy
            // that mapping so the card's Checkpoints row matches.
            if cell.worktree_checkpoints != ahead {
                cell.worktree_checkpoints = ahead;
                mutated = true;
            }
            if cell.worktree_ahead != ahead {
                cell.worktree_ahead = ahead;
                mutated = true;
            }
            if cell.worktree_behind != behind {
                cell.worktree_behind = behind;
                mutated = true;
            }
            if mutated {
                st.emit_agent(&agent_id);
            }
            mutated
        };
        let _ = changed;
    }

    // Drain any accumulated deltas in one broadcast.
    let mut st = app.state.lock().await;
    if let Some((seq, ops)) = st.drain_deltas() {
        app.bus
            .send(loom_core::events::OutMessage::Delta { seq, ops });
    }
}

async fn has_uncommitted(worktree_path: &str) -> bool {
    let Ok(out) = tokio::process::Command::new("git")
        .args(["-C", worktree_path, "status", "--porcelain"])
        .output()
        .await
    else {
        return false;
    };
    !out.stdout.is_empty()
}

/// Backfill helper for legacy worktrees that were persisted without a
/// `worktree_base_branch`. Prefers the upstream of the worktree's branch;
/// falls back to `main` / `master` by existence check. Duplicated from
/// `commands::worktree::resolve_fork_point` to keep the two call sites
/// free of a cross-module dependency on command helpers.
async fn resolve_fork_point(worktree_path: &std::path::Path) -> Option<String> {
    let path = worktree_path.to_string_lossy().to_string();
    if let Ok(out) = tokio::process::Command::new("git")
        .args(["-C", &path, "rev-parse", "--abbrev-ref", "@{upstream}"])
        .output()
        .await
    {
        if out.status.success() {
            let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !name.is_empty() {
                return Some(name);
            }
        }
    }
    for candidate in ["main", "master"] {
        if let Ok(out) = tokio::process::Command::new("git")
            .args(["-C", &path, "rev-parse", "--verify", candidate])
            .output()
            .await
        {
            if out.status.success() {
                return Some(candidate.to_string());
            }
        }
    }
    None
}

async fn tick(state: &Arc<Mutex<MatrixState>>, db: &LoomDb, bus: &EventBus) {
    let now = chrono::Utc::now();
    let fires: Vec<String> = {
        let st = state.lock().await;
        st.board_tasks
            .values()
            .filter(|t| {
                !t.scheduled_at.is_empty()
                    && t.lane == "Backlog"
                    && chrono::DateTime::parse_from_rfc3339(&t.scheduled_at)
                        .map(|dt| dt.with_timezone(&chrono::Utc) <= now)
                        .unwrap_or(false)
            })
            .map(|t| t.id.clone())
            .collect()
    };

    let mut to_save = Vec::new();
    if !fires.is_empty() {
        let mut st = state.lock().await;
        for id in &fires {
            if let Some(t) = st.board_tasks.get_mut(id) {
                t.lane = "To Do".into();
                t.scheduled_at.clear();
                t.lane_entered_at = now.to_rfc3339();
                t.updated_at = t.lane_entered_at.clone();
                to_save.push(t.clone());
            }
            st.emit_task(id);
        }
    }

    fire_due_schedules(state, db, &now).await;

    {
        let mut st = state.lock().await;
        if let Some((seq, ops)) = st.drain_deltas() {
            bus.send(loom_core::events::OutMessage::Delta { seq, ops });
        }
    }

    for t in to_save {
        let _ = db.save_board_task(&t).await;
    }
}

/// Fire every `Schedule` whose `next_run_at` / `scheduled_at` has arrived.
/// Creates a board task via `board_add_task` semantics, dispatches it (no
/// PTY — the scheduler tick is HTTP-free, so we just put it in `To Do`;
/// the user or an auto-dispatch queue picks it up), and updates the schedule
/// record's run metadata. Mirrors Python `_scheduler_loop`.
async fn fire_due_schedules(
    state: &Arc<Mutex<MatrixState>>,
    db: &LoomDb,
    now: &chrono::DateTime<chrono::Utc>,
) {
    let now_iso = now.to_rfc3339();
    let due_ids: Vec<String> = {
        let st = state.lock().await;
        st.schedules
            .values()
            .filter(|s| s.enabled && schedule_is_due(s, &now_iso))
            .map(|s| s.id.clone())
            .collect()
    };
    if due_ids.is_empty() {
        return;
    }

    for sid in due_ids {
        if let Err(err) = fire_one_schedule(state, db, &sid, now).await {
            tracing::warn!(schedule = %sid, ?err, "schedule fire failed");
        }
    }
}

fn schedule_is_due(sched: &loom_core::state::Schedule, now_iso: &str) -> bool {
    // Prefer next_run_at (populated for cron schedules), fall back to
    // scheduled_at (one-shot) — matching Python's `schedule_get_due`.
    let candidate = if !sched.next_run_at.is_empty() {
        &sched.next_run_at
    } else if !sched.scheduled_at.is_empty() {
        &sched.scheduled_at
    } else {
        return false;
    };
    candidate.as_str() <= now_iso
}

async fn fire_one_schedule(
    state: &Arc<Mutex<MatrixState>>,
    db: &LoomDb,
    sched_id: &str,
    now: &chrono::DateTime<chrono::Utc>,
) -> anyhow::Result<()> {
    use loom_core::delta::DeltaOp;
    use loom_core::state::BoardTask;
    use loom_core::task_ids::{format_root_task_id, normalize_group_prefix};

    let now_iso = now.to_rfc3339();

    // Phase 1: allocate the task + update schedule under the state lock.
    let (task, updated_sched, prefix, trunk) = {
        let mut st = state.lock().await;
        let Some(sched) = st.schedules.get(sched_id).cloned() else {
            return Ok(());
        };
        if !st.groups.contains_key(&sched.group) {
            tracing::warn!(
                schedule = %sched.name,
                group = %sched.group,
                "schedule group missing — skipping"
            );
            return Ok(());
        }

        let prefix = normalize_group_prefix(&sched.group);
        let counter = st.task_id_counters.entry(prefix.clone()).or_insert(0);
        *counter += 1;
        let trunk = *counter;
        let task_id = format_root_task_id(&sched.group, trunk);

        let title_template = if sched.task_template.is_empty() {
            sched.name.clone()
        } else {
            sched.task_template.clone()
        };
        let title = title_template
            .replace("{date}", &now.format("%Y-%m-%d").to_string())
            .replace("{time}", &now.format("%H:%M").to_string())
            .replace("{datetime}", &now.format("%Y-%m-%d %H:%M").to_string());

        let mut task = BoardTask::new_minimal(task_id.clone(), title);
        task.group = sched.group.clone();
        task.pipeline_root_id = task_id.clone();
        task.lane = "Backlog".into();
        task.action_name = sched.action_name.clone();
        task.action_vars = sched.action_vars.clone();
        task.agent_template = sched.agent_template.clone();
        task.labels = sched.labels.clone();
        task.description = sched.description.clone();
        task.created_at = now_iso.clone();
        task.updated_at = now_iso.clone();
        task.lane_entered_at = now_iso.clone();
        st.upsert_task(task.clone())?;

        // Mutate the schedule record.
        let mut updated = sched.clone();
        updated.last_run_at = now_iso.clone();
        updated.run_count += 1;
        updated.last_task_id = task_id.clone();
        updated.updated_at = now_iso.clone();
        if !updated.cron_expr.is_empty() {
            match loom_core::cron::next_run(&updated.cron_expr, *now, &updated.timezone) {
                Ok(nxt) => updated.next_run_at = nxt.to_rfc3339(),
                Err(err) => {
                    tracing::error!(
                        schedule = %updated.name,
                        cron = %updated.cron_expr,
                        %err,
                        "invalid cron — disabling"
                    );
                    updated.enabled = false;
                    updated.next_run_at.clear();
                }
            }
        } else {
            // One-shot — consumed.
            updated.enabled = false;
            updated.next_run_at.clear();
        }
        st.schedules.insert(sched_id.to_string(), updated.clone());
        st.emit(DeltaOp::ScheduleUpsert(
            serde_json::to_value(&updated).unwrap_or(serde_json::Value::Null),
        ));

        (
            st.board_tasks.get(&task_id).cloned().unwrap_or(task),
            updated,
            prefix,
            trunk,
        )
    };

    // Phase 2: persist outside the state lock.
    db.save_task_id_counter(&prefix, trunk as u64).await?;
    db.save_board_task(&task).await?;
    db.save_schedule(&updated_sched).await?;
    tracing::info!(
        schedule = %updated_sched.name,
        task = %task.id,
        "schedule fired"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use loom_core::state::BoardTask;

    #[tokio::test]
    async fn tick_fires_past_scheduled_task() {
        let state = Arc::new(Mutex::new(MatrixState::new()));
        let db = LoomDb::in_memory().unwrap();
        let bus = EventBus::new();

        {
            let mut st = state.lock().await;
            st.add_group("Eng").unwrap();
            let mut task = BoardTask::new_minimal("eng-1", "Scheduled work");
            task.group = "Eng".into();
            task.lane = "Backlog".into();
            task.scheduled_at = (chrono::Utc::now() - chrono::Duration::seconds(60)).to_rfc3339();
            st.upsert_task(task).unwrap();
            st.drain_deltas();
        }

        tick(&state, &db, &bus).await;

        let st = state.lock().await;
        let task = st.board_tasks.get("eng-1").unwrap();
        assert_eq!(task.lane, "To Do");
        assert!(task.scheduled_at.is_empty());
    }

    #[tokio::test]
    async fn tick_does_not_fire_future_scheduled_task() {
        let state = Arc::new(Mutex::new(MatrixState::new()));
        let db = LoomDb::in_memory().unwrap();
        let bus = EventBus::new();

        {
            let mut st = state.lock().await;
            st.add_group("Eng").unwrap();
            let mut task = BoardTask::new_minimal("eng-1", "Future work");
            task.group = "Eng".into();
            task.lane = "Backlog".into();
            task.scheduled_at = (chrono::Utc::now() + chrono::Duration::seconds(3600)).to_rfc3339();
            st.upsert_task(task).unwrap();
            st.drain_deltas();
        }

        tick(&state, &db, &bus).await;

        let st = state.lock().await;
        let task = st.board_tasks.get("eng-1").unwrap();
        assert_eq!(task.lane, "Backlog");
    }
}
