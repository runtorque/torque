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

use crate::app::AppState;

const TICK: Duration = Duration::from_secs(15);
const WEAVER_FLUSH_TICK: Duration = Duration::from_secs(5);

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

    if fires.is_empty() {
        return;
    }

    let mut to_save = Vec::new();
    {
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
        if let Some((seq, ops)) = st.drain_deltas() {
            bus.send(loom_core::events::OutMessage::Delta { seq, ops });
        }
    }

    for t in to_save {
        let _ = db.save_board_task(&t).await;
    }
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
