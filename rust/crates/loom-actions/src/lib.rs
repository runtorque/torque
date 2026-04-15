//! YAML action loading + minijinja rendering.

pub mod context;
pub mod manager;
pub mod render;

pub use manager::{ActionInfo, ActionManager, ActionScope};
pub use render::{RenderError, RenderOutput};
