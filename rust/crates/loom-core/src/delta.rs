//! Typed delta ops emitted by state mutations.
//!
//! Wire format mirrors `loom/state.py::_emit`: JSON objects with an `op`
//! discriminator and op-specific fields. The existing JS frontend consumes
//! these verbatim.

use serde::Serialize;

/// Typed delta op. Serializes to JSON shape the JS frontend expects.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum DeltaOp {
    AgentUpsert(serde_json::Value),
    AgentRemove {
        id: String,
    },
    GroupUpdate {
        name: String,
        slug: String,
        agents: Vec<String>,
    },
    GroupRemove {
        name: String,
    },
    GroupRename {
        from: String,
        to: String,
    },
    GroupsReorder {
        order: Vec<String>,
    },
    GroupSettingsUpdate {
        name: String,
        settings: serde_json::Value,
    },
    GlobalSettingsUpdate {
        settings: serde_json::Value,
    },
    TaskUpsert(serde_json::Value),
    TaskRemove {
        id: String,
    },
    LanesUpdate {
        lanes: Vec<String>,
    },
    UiUpdate(serde_json::Value),
    FocusUpdate {
        id: Option<String>,
    },
    ScheduleUpsert(serde_json::Value),
    ScheduleRemove {
        id: String,
    },
    WeaverSettingsUpdate {
        group: String,
        settings: serde_json::Value,
    },
    WeaverWorklogAppend {
        group: String,
        entry: serde_json::Value,
    },
    EventsUpdate(serde_json::Value),
    PanelUpdate(serde_json::Value),
    MemoryUpsert(serde_json::Value),
    MemoryRemove { id: String },
}

/// A full delta message sent to WS clients.
#[derive(Debug, Clone, Serialize)]
pub struct DeltaMessage {
    #[serde(rename = "type")]
    pub ty: &'static str, // always "delta"
    pub seq: u64,
    pub ops: Vec<DeltaOp>,
}

impl DeltaMessage {
    pub fn new(seq: u64, ops: Vec<DeltaOp>) -> Self {
        Self { ty: "delta", seq, ops }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agent_upsert_serializes_with_op_tag() {
        let op = DeltaOp::AgentUpsert(serde_json::json!({"id": "a1", "name": "test"}));
        let v = serde_json::to_value(&op).unwrap();
        assert_eq!(v["op"], "agent_upsert");
        assert_eq!(v["id"], "a1");
    }

    #[test]
    fn task_remove_has_id() {
        let op = DeltaOp::TaskRemove { id: "t1".into() };
        let v = serde_json::to_value(&op).unwrap();
        assert_eq!(v["op"], "task_remove");
        assert_eq!(v["id"], "t1");
    }

    #[test]
    fn message_shape_matches_python() {
        let msg = DeltaMessage::new(5, vec![DeltaOp::TaskRemove { id: "t".into() }]);
        let v = serde_json::to_value(&msg).unwrap();
        assert_eq!(v["type"], "delta");
        assert_eq!(v["seq"], 5);
        assert_eq!(v["ops"][0]["op"], "task_remove");
    }
}
