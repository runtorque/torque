//! PTY management via portable-pty. Provides the LocalPty terminal backend.

pub mod backend;
pub mod session;

pub use backend::LocalPtyBackend;
pub use session::{PtyEvent, PtySession};
