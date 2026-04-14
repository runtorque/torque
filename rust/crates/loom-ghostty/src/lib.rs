//! libghostty FFI + safe Rust wrappers.
//!
//! Two layers:
//! - [`ffi`] exposes the raw bindgen-generated C bindings (behind the `ffi`
//!   feature) plus a small stub surface registry.
//! - [`app`] and [`surface`] provide safe Rust wrappers (`GhosttyApp`,
//!   `Surface`) used by the UI crate.
//!
//! Without the `ffi` feature, the crate builds as pure-Rust stubs so the
//! workspace stays buildable on CI / non-macOS hosts.

pub mod backend;
pub mod ffi;

#[cfg(feature = "ffi")]
pub mod app;
#[cfg(feature = "ffi")]
pub mod surface;

pub use backend::GhosttyBackend;

#[cfg(feature = "ffi")]
pub use app::GhosttyApp;
#[cfg(feature = "ffi")]
pub use surface::Surface;

/// Re-export generated FFI enums used by callers building key events.
#[cfg(feature = "ffi")]
pub mod sys_types {
    pub use super::ffi::sys::{
        ghostty_input_action_e, ghostty_input_key_s, ghostty_input_mods_e,
    };
}
