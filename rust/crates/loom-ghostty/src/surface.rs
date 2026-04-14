//! `Surface` — safe wrapper over `ghostty_surface_t`.
//!
//! The surface is bound to a native view (NSView on macOS). Ghostty manages
//! Metal rendering, PTY child process, and input translation internally.

#![cfg(feature = "ffi")]

use std::ffi::{c_void, CString};
use std::ptr;

use super::app::GhosttyApp;
use super::ffi::sys;

pub struct Surface {
    raw: sys::ghostty_surface_t,
}

unsafe impl Send for Surface {}

impl Surface {
    /// Create a new surface bound to a macOS `NSView` pointer. Safe ownership:
    /// Ghostty adds a Metal-backed child view; the caller's NSView must
    /// outlive the surface.
    ///
    /// `command` is what Ghostty spawns in the embedded PTY (e.g. `/bin/zsh`,
    /// `claude`, `codex`). `scale_factor` is the `backingScaleFactor` of the
    /// containing window.
    pub unsafe fn new_macos(
        nsview: *mut c_void,
        scale_factor: f64,
        font_size: f32,
        command: Option<&str>,
        working_dir: Option<&str>,
    ) -> anyhow::Result<Self> {
        let cmd_c = command.map(|c| CString::new(c).unwrap());
        let cwd_c = working_dir.map(|c| CString::new(c).unwrap());

        let mut config = sys::ghostty_surface_config_new();
        config.platform_tag = sys::ghostty_platform_e::GHOSTTY_PLATFORM_MACOS;
        config.platform.macos.nsview = nsview;
        config.scale_factor = scale_factor;
        config.font_size = font_size;
        config.command = cmd_c.as_ref().map_or(ptr::null(), |c| c.as_ptr());
        config.working_directory = cwd_c.as_ref().map_or(ptr::null(), |c| c.as_ptr());
        config.env_vars = ptr::null_mut();
        config.env_var_count = 0;
        config.initial_input = ptr::null();
        config.wait_after_command = false;
        config.context = sys::ghostty_surface_context_e::GHOSTTY_SURFACE_CONTEXT_WINDOW;
        config.userdata = ptr::null_mut();

        let surface = sys::ghostty_surface_new(GhosttyApp::raw(), &config);
        if surface.is_null() {
            anyhow::bail!("ghostty_surface_new returned null");
        }
        Ok(Self { raw: surface })
    }

    pub fn raw(&self) -> sys::ghostty_surface_t {
        self.raw
    }

    pub fn set_size(&self, width_px: u32, height_px: u32) {
        unsafe { sys::ghostty_surface_set_size(self.raw, width_px, height_px) };
    }

    pub fn set_content_scale(&self, x: f64, y: f64) {
        unsafe { sys::ghostty_surface_set_content_scale(self.raw, x, y) };
    }

    pub fn set_focus(&self, focused: bool) {
        unsafe { sys::ghostty_surface_set_focus(self.raw, focused) };
    }

    pub fn draw(&self) {
        unsafe { sys::ghostty_surface_draw(self.raw) };
    }

    pub fn refresh(&self) {
        unsafe { sys::ghostty_surface_refresh(self.raw) };
    }

    /// Write text to the surface (IME/composition path — displayed as input
    /// preedit, not sent to the PTY child).
    pub fn write_text(&self, s: &str) {
        unsafe {
            sys::ghostty_surface_text(self.raw, s.as_ptr() as *const _, s.len() as _);
        }
    }

    /// Send a key event (press/release) to the surface. This is the right path
    /// for actually typing at the shell — Ghostty translates it into the PTY
    /// byte stream including Enter, Tab, arrow-key escape sequences, etc.
    ///
    /// `text` is an optional UTF-8 character string (typically from
    /// `NSEvent.characters`), used for Unicode input. Arrow keys / Return /
    /// Tab don't need `text` — Ghostty derives bytes from the keycode.
    pub fn send_key(
        &self,
        action: sys::ghostty_input_action_e,
        mods: sys::ghostty_input_mods_e,
        keycode: u32,
        text: Option<&std::ffi::CStr>,
    ) -> bool {
        let key = sys::ghostty_input_key_s {
            action,
            mods,
            consumed_mods: sys::ghostty_input_mods_e::GHOSTTY_MODS_NONE,
            keycode,
            text: text.map_or(std::ptr::null(), |s| s.as_ptr()),
            unshifted_codepoint: 0,
            composing: false,
        };
        unsafe { sys::ghostty_surface_key(self.raw, key) }
    }
}

impl Drop for Surface {
    fn drop(&mut self) {
        unsafe { sys::ghostty_surface_free(self.raw) };
    }
}
