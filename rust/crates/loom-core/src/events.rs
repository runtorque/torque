//! Event bus + throttled broadcast.
//!
//! Broadcasts state deltas to WS subscribers. A lagged receiver (queue full)
//! triggers a forced resync on the server side — mirroring the Python
//! `seq` gap behavior.

use std::sync::Arc;

use serde::Serialize;
use tokio::sync::{broadcast, Mutex};

use crate::delta::{DeltaMessage, DeltaOp};
use crate::state::MatrixState;

const BROADCAST_CAPACITY: usize = 256;

/// Wire message the server sends to WS clients.
///
/// Identical JSON shape to `loom/server.py` (type + seq + ops for deltas,
/// type + state for snapshots).
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OutMessage {
    Delta { seq: u64, ops: Vec<DeltaOp> },
    Snapshot { seq: u64, state: serde_json::Value },
    Event(serde_json::Value),
}

/// Broadcast bus — cloneable sender + clonable receivers from `subscribe()`.
#[derive(Clone)]
pub struct EventBus {
    tx: broadcast::Sender<OutMessage>,
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}

impl EventBus {
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(BROADCAST_CAPACITY);
        Self { tx }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<OutMessage> {
        self.tx.subscribe()
    }

    pub fn send(&self, msg: OutMessage) {
        let _ = self.tx.send(msg);
    }

    pub fn subscriber_count(&self) -> usize {
        self.tx.receiver_count()
    }
}

/// Drain any pending deltas and broadcast them.
/// Returns the seq if a message was emitted.
pub async fn flush_deltas(state: Arc<Mutex<MatrixState>>, bus: &EventBus) -> Option<u64> {
    let mut st = state.lock().await;
    let (seq, ops) = st.drain_deltas()?;
    bus.send(OutMessage::Delta { seq, ops });
    Some(seq)
}

/// Build a full snapshot message for a new subscriber or a resync.
pub fn snapshot_message(_state: &MatrixState, seq: u64, value: serde_json::Value) -> DeltaMessage {
    DeltaMessage::new(seq, vec![DeltaOp::UiUpdate(value)])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn bus_broadcasts_to_subscribers() {
        let bus = EventBus::new();
        let mut rx1 = bus.subscribe();
        let mut rx2 = bus.subscribe();
        bus.send(OutMessage::Delta {
            seq: 1,
            ops: vec![],
        });
        let m1 = rx1.recv().await.unwrap();
        let m2 = rx2.recv().await.unwrap();
        match (m1, m2) {
            (OutMessage::Delta { seq: s1, .. }, OutMessage::Delta { seq: s2, .. }) => {
                assert_eq!(s1, 1);
                assert_eq!(s2, 1);
            }
            _ => panic!("expected delta"),
        }
    }

    #[tokio::test]
    async fn flush_deltas_drains_state() {
        let state = Arc::new(Mutex::new(MatrixState::new()));
        {
            let mut s = state.lock().await;
            s.add_group("Eng").unwrap();
        }
        let bus = EventBus::new();
        let mut rx = bus.subscribe();
        let seq = flush_deltas(state.clone(), &bus).await.unwrap();
        assert_eq!(seq, 1);
        let msg = rx.recv().await.unwrap();
        matches!(msg, OutMessage::Delta { seq: 1, .. });
        // nothing more to drain
        assert!(flush_deltas(state, &bus).await.is_none());
    }
}
