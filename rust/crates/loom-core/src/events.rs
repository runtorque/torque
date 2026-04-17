//! Event bus + throttled broadcast.
//!
//! Broadcasts state deltas to WS subscribers. A lagged receiver (queue full)
//! triggers a forced resync on the server side — mirroring the Python
//! `seq` gap behavior.
//!
//! Delta broadcasts are coalesced with a 1s trailing-edge throttle (matches
//! Python `EventBus._schedule_broadcast`): many mutations within the same
//! second share a single WS frame. Snapshot messages bypass the throttle —
//! they go out immediately to new connections / resync requests.

use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use tokio::sync::{broadcast, Mutex, RwLock};

use crate::delta::{DeltaMessage, DeltaOp};
use crate::state::MatrixState;

const BROADCAST_CAPACITY: usize = 256;
const BROADCAST_THROTTLE: Duration = Duration::from_millis(1000);

/// Async callback executed right before `request_delta_flush` drains
/// accumulated deltas. The hook can mutate state (e.g. recompute task
/// health) and the resulting mutations ride the same broadcast.
pub type PreFlushHook = Arc<
    dyn Fn(Arc<Mutex<MatrixState>>) -> Pin<Box<dyn Future<Output = ()> + Send>>
        + Send
        + Sync
        + 'static,
>;

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
    pending_delta: Arc<AtomicBool>,
    pre_flush: Arc<RwLock<Option<PreFlushHook>>>,
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}

impl EventBus {
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(BROADCAST_CAPACITY);
        Self {
            tx,
            pending_delta: Arc::new(AtomicBool::new(false)),
            pre_flush: Arc::new(RwLock::new(None)),
        }
    }

    /// Install (or replace) the pre-flush hook. Typically called once at
    /// startup with a closure that runs `recompute_task_health` + any other
    /// pre-broadcast refresh work.
    pub async fn set_pre_flush_hook(&self, hook: PreFlushHook) {
        let mut slot = self.pre_flush.write().await;
        *slot = Some(hook);
    }

    pub fn subscribe(&self) -> broadcast::Receiver<OutMessage> {
        self.tx.subscribe()
    }

    /// Send a message directly, bypassing the delta throttle.
    ///
    /// Use this for `Snapshot` / `Event` messages, and for tests that need
    /// deterministic synchronous delivery. For normal delta traffic during
    /// command execution, prefer `request_delta_flush` so bursts coalesce.
    pub fn send(&self, msg: OutMessage) {
        let _ = self.tx.send(msg);
    }

    pub fn subscriber_count(&self) -> usize {
        self.tx.receiver_count()
    }

    /// Request a throttled drain-and-broadcast of state deltas.
    ///
    /// First caller in a quiet window schedules a background task that sleeps
    /// `BROADCAST_THROTTLE` and then drains + sends whatever deltas
    /// accumulated. Subsequent callers during that window are a no-op: the
    /// pending task will sweep up their mutations too. Matches Python's
    /// 1s trailing-edge throttle in `EventBus._schedule_broadcast`.
    pub fn request_delta_flush(&self, state: Arc<Mutex<MatrixState>>) {
        if self
            .pending_delta
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            // A flush is already scheduled — it will sweep these deltas up.
            return;
        }
        let tx = self.tx.clone();
        let flag = self.pending_delta.clone();
        let pre_flush = self.pre_flush.clone();
        tokio::spawn(async move {
            tokio::time::sleep(BROADCAST_THROTTLE).await;
            // Clear flag *before* draining so a brand-new mutation arriving
            // during the send schedules a fresh follow-up flush. If we
            // cleared after, that mutation would see `pending=true` and the
            // trailing broadcast would be lost.
            flag.store(false, Ordering::Release);
            // Run any pre-flush hook (e.g. task-health recompute) so its
            // mutations ride the same broadcast. Matches Python's
            // `_fire_broadcast` coalescing `recompute_task_health` with the
            // 1s trailing-edge timer.
            let hook = { pre_flush.read().await.clone() };
            if let Some(hook) = hook {
                hook(state.clone()).await;
            }
            let drained = {
                let mut st = state.lock().await;
                st.drain_deltas()
            };
            if let Some((seq, ops)) = drained {
                let _ = tx.send(OutMessage::Delta { seq, ops });
            }
        });
    }
}

/// Drain any pending deltas and broadcast them immediately (bypasses the
/// throttle). Keep this for tests and scheduler-driven flushes that want
/// synchronous delivery. Command paths should prefer
/// `EventBus::request_delta_flush`.
pub async fn flush_deltas(state: Arc<Mutex<MatrixState>>, bus: &EventBus) -> Option<u64> {
    let mut st = state.lock().await;
    let (seq, ops) = st.drain_deltas()?;
    bus.send(OutMessage::Delta { seq, ops });
    Some(seq)
}

/// Build a full snapshot message for a new subscriber or a resync.
pub fn snapshot_message(_state: &MatrixState, seq: u64, value: serde_json::Value) -> DeltaMessage {
    DeltaMessage::new(
        seq,
        vec![DeltaOp::UiUpdate {
            key: "snapshot".into(),
            value,
        }],
    )
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
