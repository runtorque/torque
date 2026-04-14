//! Weaver hints. Port of `loom/weaver_hints.py`.

use loom_core::state::MatrixState;

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct WeaverHints {
    pub stream_count: usize,
    pub blocked_count: usize,
    pub ready_count: usize,
}

pub fn compute_weaver_hints(_state: &MatrixState, _group: &str) -> WeaverHints {
    // TODO: port full scan
    WeaverHints::default()
}
