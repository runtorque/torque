//! Weaver session map. Port of `loom/weaver_session_map.py`.

use loom_core::state::MatrixState;

pub fn build_weaver_session_map(_state: &MatrixState, _group: &str) -> serde_json::Value {
    serde_json::json!({
        "agents": [],
        "tasks": [],
        "streams": [],
    })
}
