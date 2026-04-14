//! `GhosttyView` — AppKit NSView hosting a libghostty surface.
//!
//! We create an empty container NSView and pass its raw pointer to
//! `ghostty_surface_new`. Ghostty adds its own Metal-backed subview for the
//! actual rendering; we just forward key/mouse events into the surface.
//!
//! Lifecycle:
//! - Init with a command to run (e.g. `/bin/zsh`) and a working dir.
//! - `viewDidMoveToWindow`: if we have a window, create the surface.
//! - `keyDown:` forwards characters into the surface.
//! - On drop: `ghostty_surface_free` (via `Surface::drop`).

#![cfg(all(target_os = "macos", feature = "appkit"))]

use std::cell::RefCell;
use std::ffi::c_void;

use objc2::rc::Retained;
use objc2::{declare_class, msg_send_id, mutability, ClassType, DeclaredClass};
use objc2_app_kit::{NSEvent, NSView, NSWindow};
use objc2_foundation::{MainThreadMarker, NSRect, NSSize};

use loom_ghostty::{GhosttyApp, Surface};

pub struct GhosttyViewIvars {
    surface: RefCell<Option<Surface>>,
    command: RefCell<String>,
    working_dir: RefCell<Option<String>>,
}

declare_class!(
    pub struct GhosttyView;

    unsafe impl ClassType for GhosttyView {
        type Super = NSView;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomGhosttyView";
    }

    impl DeclaredClass for GhosttyView {
        type Ivars = GhosttyViewIvars;
    }

    unsafe impl GhosttyView {
        #[method(viewDidMoveToWindow)]
        fn view_did_move_to_window(&self) {
            self.ensure_surface();
        }

        #[method(acceptsFirstResponder)]
        fn accepts_first_responder(&self) -> bool {
            true
        }

        #[method(keyDown:)]
        fn key_down(&self, event: &NSEvent) {
            let Some(surface_ref) = self.ivars().surface.borrow().as_ref().map(|s| s.raw()) else {
                return;
            };
            // Keep the surface borrow released before calling into ghostty —
            // callbacks may re-enter Rust.
            drop(surface_ref);
            forward_key(self, event, true);
        }

        #[method(keyUp:)]
        fn key_up(&self, event: &NSEvent) {
            forward_key(self, event, false);
        }

        #[method(flagsChanged:)]
        fn flags_changed(&self, _event: &NSEvent) {
            // Modifier change: v1 ignores. Full support would translate to
            // ghostty_surface_key with the transitioning modifier.
        }

        #[method(setFrameSize:)]
        fn set_frame_size(&self, new_size: NSSize) {
            let _: () = unsafe { objc2::msg_send![super(self), setFrameSize: new_size] };
            let this_view: &NSView = self;
            let window: Option<Retained<NSWindow>> = this_view.window();
            if let Some(surface) = self.ivars().surface.borrow().as_ref() {
                let scale = window
                    .as_ref()
                    .map(|w| w.backingScaleFactor())
                    .unwrap_or(1.0);
                let w = (new_size.width * scale) as u32;
                let h = (new_size.height * scale) as u32;
                surface.set_size(w, h);
            }
        }
    }
);

impl GhosttyView {
    pub fn new(
        mtm: MainThreadMarker,
        frame: NSRect,
        command: impl Into<String>,
        working_dir: Option<String>,
    ) -> Retained<Self> {
        let ivars = GhosttyViewIvars {
            surface: RefCell::new(None),
            command: RefCell::new(command.into()),
            working_dir: RefCell::new(working_dir),
        };
        let this = mtm.alloc::<Self>().set_ivars(ivars);
        let this: Retained<Self> = unsafe { msg_send_id![super(this), initWithFrame: frame] };
        unsafe {
            this.setWantsLayer(true);
        }
        this
    }

    /// Create the libghostty surface once we have a window + scale factor.
    fn ensure_surface(&self) {
        if self.ivars().surface.borrow().is_some() {
            return;
        }
        let this_view: &NSView = self;
        let window: Option<Retained<NSWindow>> = this_view.window();
        let Some(window) = window else {
            return;
        };
        if let Err(err) = GhosttyApp::init() {
            tracing::error!("GhosttyApp::init failed: {err}");
            return;
        }
        let scale = window.backingScaleFactor();
        let cmd = self.ivars().command.borrow().clone();
        let cwd = self.ivars().working_dir.borrow().clone();

        let nsview_ptr: *mut c_void = self as *const Self as *const c_void as *mut c_void;

        let surface = unsafe {
            Surface::new_macos(nsview_ptr, scale, 13.0, Some(&cmd), cwd.as_deref())
        };
        match surface {
            Ok(s) => {
                s.set_focus(true);
                *self.ivars().surface.borrow_mut() = Some(s);
            }
            Err(err) => tracing::error!("Surface::new_macos failed: {err}"),
        }
    }

    /// Programmatic input — used by the dispatch pipeline to send prompts.
    pub fn send_text(&self, text: &str) {
        if let Some(surface) = self.ivars().surface.borrow().as_ref() {
            surface.write_text(text);
        }
    }
}

fn forward_key(view: &GhosttyView, event: &NSEvent, is_down: bool) {
    use loom_ghostty::sys_types::{ghostty_input_action_e, ghostty_input_mods_e};
    use objc2_app_kit::NSEventModifierFlags;

    let Some(surface) = view.ivars().surface.borrow().as_ref().map(|s| {
        // Re-read on each call, but we need to hold the borrow during the FFI
        // call. RefCell is fine here — Cocoa events are serialised on main.
        s as *const loom_ghostty::Surface
    }) else {
        return;
    };

    let ns_flags = unsafe { event.modifierFlags() };
    let mut mods = ghostty_input_mods_e::GHOSTTY_MODS_NONE as u32;
    if ns_flags.contains(NSEventModifierFlags::NSEventModifierFlagShift) {
        mods |= ghostty_input_mods_e::GHOSTTY_MODS_SHIFT as u32;
    }
    if ns_flags.contains(NSEventModifierFlags::NSEventModifierFlagControl) {
        mods |= ghostty_input_mods_e::GHOSTTY_MODS_CTRL as u32;
    }
    if ns_flags.contains(NSEventModifierFlags::NSEventModifierFlagOption) {
        mods |= ghostty_input_mods_e::GHOSTTY_MODS_ALT as u32;
    }
    if ns_flags.contains(NSEventModifierFlags::NSEventModifierFlagCommand) {
        mods |= ghostty_input_mods_e::GHOSTTY_MODS_SUPER as u32;
    }
    if ns_flags.contains(NSEventModifierFlags::NSEventModifierFlagCapsLock) {
        mods |= ghostty_input_mods_e::GHOSTTY_MODS_CAPS as u32;
    }
    // Safety: ghostty_input_mods_e is `#[repr(C)]` in bindgen output; we rebuild
    // it from a bitmask of its variants.
    let mods = unsafe { std::mem::transmute::<u32, ghostty_input_mods_e>(mods) };

    let keycode = unsafe { event.keyCode() } as u32;
    let chars = unsafe { event.charactersIgnoringModifiers() };
    let text_c = chars
        .as_ref()
        .map(|ns| std::ffi::CString::new(ns.to_string()).ok())
        .flatten();

    let action = if is_down {
        ghostty_input_action_e::GHOSTTY_ACTION_PRESS
    } else {
        ghostty_input_action_e::GHOSTTY_ACTION_RELEASE
    };

    unsafe {
        (*surface).send_key(action, mods, keycode, text_c.as_deref());
    }
}
