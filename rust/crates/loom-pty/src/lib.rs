//! PTY management via portable-pty. Provides the LocalPty terminal backend.

pub mod backend;
pub mod session;
pub mod shell;

pub use backend::LocalPtyBackend;
pub use session::{PtyEvent, PtySession};
pub use shell::build_shell_argv;
