//! Task-health scoring. Port of `loom/task_health.py`.

use loom_core::state::BoardTask;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthSeverity {
    Ok,
    Warn,
    Critical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthState {
    pub severity: HealthSeverity,
    pub reason: String,
    pub since: String,
}

pub fn score_task(_task: &BoardTask) -> HealthState {
    HealthState {
        severity: HealthSeverity::Ok,
        reason: String::new(),
        since: String::new(),
    }
}
