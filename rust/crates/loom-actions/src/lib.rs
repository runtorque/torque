//! YAML action loading + minijinja rendering.

pub mod manager;
pub mod render;
pub mod context;

pub use manager::{ActionManager, ActionScope, ActionInfo};
pub use render::{RenderOutput, RenderError};
