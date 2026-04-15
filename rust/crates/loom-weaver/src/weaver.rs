//! Weaver coordinator. Port lives in Phase 6.
//!
//! The weaver watches a group's board + agents, computes digests, and either
//! suggests or auto-dispatches follow-up tasks based on `WeaverSettings`.
//! See `loom/weaver.py` for the Python reference.

use loom_core::state::MatrixState;

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
