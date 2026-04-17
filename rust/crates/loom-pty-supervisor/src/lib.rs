//! Rust port of Loom's PTY supervisor sidecar + daemon-side client.
//!
//! Mirrors `loom/pty_supervisor.py` + `loom/pty_supervisor_client.py`.
//!
//! The sidecar is a detached process that owns the PTY master FDs and
//! child shell processes. It listens on a unix-domain socket and serves
//! a small JSON-over-length-prefix protocol (see `protocol`). The
//! Loom daemon connects as a client, creates/writes/resizes/subscribes
//! to sessions, and — crucially — survives its own restart: when the
//! daemon comes back up it reconnects, compares PIDs to detect "same
//! instance" vs. "supervisor respawned", and re-subscribes to every
//! session it still tracks.

pub mod backend;
pub mod client;
pub mod lifecycle;
pub mod protocol;
pub mod supervisor;

pub use backend::SupervisedPtyBackend;
pub use client::{PtySupervisorClient, ReconnectInfo, SubscriptionEvent};
pub use lifecycle::{ensure_running, spawn_detached, SupervisorPaths};
pub use supervisor::PtySupervisor;
