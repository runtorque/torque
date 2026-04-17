//! macOS notifications with 5s batching.
//!
//! Direct port of `loom/notifications.py`. Collects triggers over a 5s
//! window, then shells out to `osascript` to display a single combined
//! notification per group.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::Mutex;

use loom_core::state::MatrixState;

const BATCH_WINDOW: Duration = Duration::from_secs(5);

#[derive(Clone, Debug)]
pub struct NotificationItem {
    pub group: String,
    pub subject: String,
    pub kind: String,
    /// "agents" or "tasks" — used when >1 items roll up per group.
    pub noun: String,
}

#[derive(Default)]
struct Inner {
    pending: Vec<NotificationItem>,
    timer_active: bool,
}

#[derive(Clone, Default)]
pub struct NotificationManager {
    inner: Arc<Mutex<Inner>>,
}

impl NotificationManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// Queue a notification triggered by an agent event. Respects the group's
    /// `notifications` / `notify_on_*` flags.
    pub async fn on_event(
        &self,
        state: &MatrixState,
        cell_id: &str,
        event_kind: &str,
    ) {
        let Some(cell) = state.agents.get(cell_id) else {
            return;
        };
        let gs = state.get_group_settings(&cell.group);
        if !gs.notifications {
            return;
        }
        let kind = match event_kind {
            "session_end" if gs.notify_on_finish => "finished",
            "error" if gs.notify_on_error => "error",
            "waiting" if gs.notify_on_attention => "needs attention",
            _ => return,
        };
        self.queue(NotificationItem {
            group: cell.group.clone(),
            subject: cell.name.clone(),
            kind: kind.into(),
            noun: "agents".into(),
        })
        .await;
    }

    /// Queue a notification when health_check flags an agent. Only fires
    /// when the group has `notify_on_attention` enabled.
    pub async fn on_health_alert(&self, state: &MatrixState, cell_id: &str) {
        let Some(cell) = state.agents.get(cell_id) else {
            return;
        };
        let gs = state.get_group_settings(&cell.group);
        if !gs.notifications || !gs.notify_on_attention {
            return;
        }
        self.queue(NotificationItem {
            group: cell.group.clone(),
            subject: cell.name.clone(),
            kind: "needs attention".into(),
            noun: "agents".into(),
        })
        .await;
    }

    /// Queue a task-health notification when a task becomes risky.
    pub async fn on_task_health_alert(
        &self,
        state: &MatrixState,
        task_id: &str,
        health_state: &str,
    ) {
        let Some(task) = state.board_tasks.get(task_id) else {
            return;
        };
        let gs = state.get_group_settings(&task.group);
        if !gs.notifications || !gs.notify_on_attention {
            return;
        }
        let Some(kind) = health_notification_kind(health_state) else {
            return;
        };
        self.queue(NotificationItem {
            group: task.group.clone(),
            subject: task_subject(&task.task),
            kind: kind.into(),
            noun: "tasks".into(),
        })
        .await;
    }

    /// Send a system-level notification immediately, bypassing the 5s batch
    /// window. Used for infrastructure events (e.g. PTY supervisor restart).
    pub fn on_system_alert(&self, title: &str, body: &str) {
        let title = title.to_string();
        let body = body.to_string();
        tokio::spawn(async move { send_notification(&title, &body).await });
    }

    async fn queue(&self, item: NotificationItem) {
        let start_timer = {
            let mut inner = self.inner.lock().await;
            inner.pending.push(item);
            if inner.timer_active {
                false
            } else {
                inner.timer_active = true;
                true
            }
        };
        if !start_timer {
            return;
        }
        let this = self.clone();
        tokio::spawn(async move {
            tokio::time::sleep(BATCH_WINDOW).await;
            this.fire().await;
        });
    }

    async fn fire(&self) {
        let items = {
            let mut inner = self.inner.lock().await;
            inner.timer_active = false;
            std::mem::take(&mut inner.pending)
        };
        if items.is_empty() {
            return;
        }
        // Group by group.
        let mut by_group: HashMap<String, Vec<NotificationItem>> = HashMap::new();
        for item in items {
            by_group.entry(item.group.clone()).or_default().push(item);
        }
        for (group, group_items) in by_group {
            let title = format!("Loom — {group}");
            let body = build_body(&group_items);
            send_notification(&title, &body).await;
        }
    }
}

fn build_body(items: &[NotificationItem]) -> String {
    if items.len() == 1 {
        let item = &items[0];
        return format!("{} {}", item.subject, item.kind);
    }
    // Roll up by (kind, noun).
    let mut counts: HashMap<(String, String), usize> = HashMap::new();
    for item in items {
        *counts
            .entry((item.kind.clone(), item.noun.clone()))
            .or_insert(0) += 1;
    }
    let mut parts = Vec::new();
    for ((kind, noun), count) in counts {
        if count == 1 {
            let subject = items
                .iter()
                .find(|i| i.kind == kind && i.noun == noun)
                .map(|i| i.subject.clone())
                .unwrap_or_default();
            parts.push(format!("{subject} {kind}"));
        } else {
            parts.push(format!("{count} {noun} {kind}"));
        }
    }
    parts.join(", ")
}

fn health_notification_kind(health_state: &str) -> Option<&'static str> {
    match health_state {
        "idle-risk" => Some("at risk"),
        "stalled" => Some("stalled"),
        "thrashing" => Some("thrashing"),
        _ => None,
    }
}

fn task_subject(title: &str) -> String {
    let trimmed = title.trim();
    let shortened = if trimmed.chars().count() > 40 {
        let mut s: String = trimmed.chars().take(37).collect();
        s.push_str("...");
        s
    } else {
        trimmed.to_string()
    };
    if shortened.is_empty() {
        "Task \"untitled\"".into()
    } else {
        format!("Task \"{shortened}\"")
    }
}

fn escape(text: &str) -> String {
    text.replace('\\', "\\\\").replace('"', "\\\"")
}

async fn send_notification(title: &str, body: &str) {
    #[cfg(target_os = "macos")]
    {
        let script = format!(
            "display notification \"{}\" with title \"{}\"",
            escape(body),
            escape(title)
        );
        match tokio::process::Command::new("osascript")
            .arg("-e")
            .arg(&script)
            .output()
            .await
        {
            Ok(out) if out.status.success() => {
                tracing::debug!(title, body, "notification sent");
            }
            Ok(out) => {
                tracing::warn!(
                    stderr = %String::from_utf8_lossy(&out.stderr).trim(),
                    "osascript notification failed"
                );
            }
            Err(err) => tracing::warn!(?err, "osascript spawn failed"),
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        tracing::debug!(title, body, "notification suppressed (non-macOS)");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn body_single_item() {
        let items = vec![NotificationItem {
            group: "g".into(),
            subject: "worker-1".into(),
            kind: "finished".into(),
            noun: "agents".into(),
        }];
        assert_eq!(build_body(&items), "worker-1 finished");
    }

    #[test]
    fn body_multiple_agents_rolls_up_counts() {
        let items = vec![
            NotificationItem {
                group: "g".into(),
                subject: "w1".into(),
                kind: "finished".into(),
                noun: "agents".into(),
            },
            NotificationItem {
                group: "g".into(),
                subject: "w2".into(),
                kind: "finished".into(),
                noun: "agents".into(),
            },
        ];
        assert_eq!(build_body(&items), "2 agents finished");
    }

    #[test]
    fn task_subject_truncates_long_titles() {
        let long = "a".repeat(60);
        let s = task_subject(&long);
        assert!(s.len() <= 50);
        assert!(s.ends_with("\""));
    }
}
