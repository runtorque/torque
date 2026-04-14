//! `GhosttyApp` — a process-wide libghostty app singleton.
//!
//! Holds `ghostty_config_t` + `ghostty_app_t`, wires the 6 runtime callbacks,
//! and exposes `tick()` / `set_focus()`. Safe Rust API.

#![cfg(feature = "ffi")]

use std::ffi::c_void;
use std::ptr;
use std::sync::{Mutex, OnceLock};

use super::ffi::sys;

pub struct GhosttyApp {
    config: sys::ghostty_config_t,
    app: sys::ghostty_app_t,
}

unsafe impl Send for GhosttyApp {}
unsafe impl Sync for GhosttyApp {}

static APP: OnceLock<Mutex<GhosttyApp>> = OnceLock::new();

impl GhosttyApp {
    /// Initialise the process-wide GhosttyApp (idempotent).
    pub fn init() -> anyhow::Result<()> {
        APP.get_or_init(|| Mutex::new(Self::new_inner().expect("ghostty_app_new failed")));
        Ok(())
    }

    /// Get the raw app pointer. Panics if init() wasn't called.
    pub fn raw() -> sys::ghostty_app_t {
        APP.get()
            .expect("GhosttyApp::init not called")
            .lock()
            .unwrap()
            .app
    }

    fn new_inner() -> anyhow::Result<Self> {
        unsafe {
            // ghostty_init is a one-time process init.
            let rc = sys::ghostty_init(0, ptr::null_mut());
            if rc != 0 {
                anyhow::bail!("ghostty_init returned {rc}");
            }

            let config = sys::ghostty_config_new();
            if config.is_null() {
                anyhow::bail!("ghostty_config_new returned null");
            }
            sys::ghostty_config_load_default_files(config);
            sys::ghostty_config_finalize(config);

            let runtime = sys::ghostty_runtime_config_s {
                userdata: ptr::null_mut(),
                supports_selection_clipboard: false,
                wakeup_cb: Some(cb_wakeup),
                action_cb: Some(cb_action),
                read_clipboard_cb: Some(cb_read_clipboard),
                confirm_read_clipboard_cb: Some(cb_confirm_read_clipboard),
                write_clipboard_cb: Some(cb_write_clipboard),
                close_surface_cb: Some(cb_close_surface),
            };

            let app = sys::ghostty_app_new(&runtime, config);
            if app.is_null() {
                sys::ghostty_config_free(config);
                anyhow::bail!("ghostty_app_new returned null");
            }

            Ok(Self { config, app })
        }
    }

    /// Drive the app's internal event loop. Call periodically from the UI thread.
    pub fn tick() {
        unsafe { sys::ghostty_app_tick(Self::raw()) };
    }

    pub fn set_focus(focused: bool) {
        unsafe { sys::ghostty_app_set_focus(Self::raw(), focused) };
    }
}

impl Drop for GhosttyApp {
    fn drop(&mut self) {
        unsafe {
            sys::ghostty_app_free(self.app);
            sys::ghostty_config_free(self.config);
        }
    }
}

// ---------------------------------------------------------------------------
// C callbacks
// ---------------------------------------------------------------------------

extern "C" fn cb_wakeup(_userdata: *mut c_void) {
    // The main thread ticks on its own refresh timer; nothing to do.
}

extern "C" fn cb_action(
    _app: sys::ghostty_app_t,
    _target: sys::ghostty_target_s,
    _action: sys::ghostty_action_s,
) -> bool {
    // V1: ignore all actions (new window, new tab, quit requests, etc.).
    // Returning false means "not handled" — ghostty may fall back to defaults.
    false
}

extern "C" fn cb_read_clipboard(
    _userdata: *mut c_void,
    _clipboard: sys::ghostty_clipboard_e,
    _state: *mut c_void,
) -> bool {
    false
}

extern "C" fn cb_confirm_read_clipboard(
    _userdata: *mut c_void,
    _s: *const std::os::raw::c_char,
    _state: *mut c_void,
    _req: sys::ghostty_clipboard_request_e,
) {
}

extern "C" fn cb_write_clipboard(
    _userdata: *mut c_void,
    _clipboard: sys::ghostty_clipboard_e,
    _contents: *const sys::ghostty_clipboard_content_s,
    _count: usize,
    _confirm: bool,
) {
}

extern "C" fn cb_close_surface(_userdata: *mut c_void, _process_alive: bool) {
    // The UI layer tracks surface lifetime itself; no-op here.
}
