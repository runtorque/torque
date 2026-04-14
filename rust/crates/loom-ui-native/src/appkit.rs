//! AppKit layer — native macOS window for Loom.
//!
//! Built with objc2 0.5 + objc2-app-kit 0.2. Object graph:
//!
//! ```text
//! NSApplication
//! ├─ menubar → App menu → Quit (Cmd-Q)
//! └─ NSWindow (Titled, Closable, Resizable, Miniaturizable)
//!    └─ NSSplitView (vertical)
//!       ├─ NSScrollView → NSTextView (sidebar, monospace, read-only)
//!       └─ NSScrollView → NSTextView (content, monospace, read-only)
//! ```
//!
//! Live updates: an `NSTimer` fires every 500 ms on the main run loop; the
//! callback pulls a fresh snapshot from the engine and repaints both text
//! views. Everything stays on the main thread — no cross-thread marshalling.

use anyhow::Result;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{declare_class, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSApplication, NSApplicationActivationPolicy, NSAutoresizingMaskOptions, NSBackingStoreType,
    NSBorderType, NSColor, NSFont, NSMenu, NSMenuItem, NSScrollView, NSSplitView,
    NSSplitViewDividerStyle, NSTextView, NSWindow, NSWindowStyleMask,
};
use objc2_foundation::{
    MainThreadMarker, NSObject, NSPoint, NSRect, NSSize, NSString, NSTimer,
};

use loom_server::app::AppState;

use crate::bridge::EngineBridge;
use crate::render;

const REFRESH_INTERVAL_SECS: f64 = 0.5;

pub fn run(engine: AppState) -> Result<()> {
    let mtm = MainThreadMarker::new().ok_or_else(|| {
        anyhow::anyhow!("native UI must run on the main thread (see loom-app/src/main.rs)")
    })?;

    let bridge = EngineBridge::new(engine);

    let app = NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(NSApplicationActivationPolicy::Regular);

    install_menubar(&app, mtm);

    let views = build_window(mtm);
    let window = views.window.clone();

    refresh_views(&bridge, &views);
    install_refresh_timer(mtm, bridge, views);

    unsafe {
        window.makeKeyAndOrderFront(None);
        #[allow(deprecated)]
        app.activateIgnoringOtherApps(true);
        app.run();
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Window + views
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct Views {
    window: Retained<NSWindow>,
    sidebar_tv: Retained<NSTextView>,
    content_tv: Retained<NSTextView>,
}

fn build_window(mtm: MainThreadMarker) -> Views {
    let content_rect = NSRect::new(NSPoint::new(100.0, 100.0), NSSize::new(1200.0, 760.0));
    let style = NSWindowStyleMask::Titled
        | NSWindowStyleMask::Closable
        | NSWindowStyleMask::Miniaturizable
        | NSWindowStyleMask::Resizable;

    let window: Retained<NSWindow> = unsafe {
        let alloc = mtm.alloc::<NSWindow>();
        NSWindow::initWithContentRect_styleMask_backing_defer(
            alloc,
            content_rect,
            style,
            NSBackingStoreType::NSBackingStoreBuffered,
            false,
        )
    };
    window.setTitle(&NSString::from_str("Loom"));

    let split: Retained<NSSplitView> = unsafe {
        let alloc = mtm.alloc::<NSSplitView>();
        let split = NSSplitView::initWithFrame(
            alloc,
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(1200.0, 760.0)),
        );
        split.setVertical(true);
        split.setDividerStyle(NSSplitViewDividerStyle::Thin);
        split
    };

    let sidebar_frame = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(280.0, 760.0));
    let sidebar_scroll = make_scroll_view(mtm, sidebar_frame);
    let sidebar_tv = make_text_view(mtm, sidebar_frame);
    unsafe { sidebar_scroll.setDocumentView(Some(&sidebar_tv)) };

    // Content area — libghostty-backed terminal.
    let content_frame = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(920.0, 760.0));
    let ghostty_view = crate::ghostty_view::GhosttyView::new(
        mtm,
        content_frame,
        "/bin/zsh",
        None,
    );
    unsafe {
        ghostty_view.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
    }

    // Keep a hidden content_tv so the existing refresh pipeline still has
    // something to talk to. It sits in a zero-size invisible subview.
    let content_tv = make_text_view(
        mtm,
        NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(1.0, 1.0)),
    );
    unsafe { content_tv.setHidden(true) };

    unsafe {
        split.addSubview(&sidebar_scroll);
        split.addSubview(&*ghostty_view);
        window.setContentView(Some(&split));
    }

    Views { window, sidebar_tv, content_tv }
}

fn make_scroll_view(mtm: MainThreadMarker, frame: NSRect) -> Retained<NSScrollView> {
    unsafe {
        let alloc = mtm.alloc::<NSScrollView>();
        let scroll = NSScrollView::initWithFrame(alloc, frame);
        scroll.setHasVerticalScroller(true);
        scroll.setAutohidesScrollers(true);
        scroll.setBorderType(NSBorderType::NSNoBorder);
        scroll
    }
}

fn make_text_view(mtm: MainThreadMarker, frame: NSRect) -> Retained<NSTextView> {
    unsafe {
        let alloc = mtm.alloc::<NSTextView>();
        let tv = NSTextView::initWithFrame(alloc, frame);
        tv.setEditable(false);
        tv.setSelectable(true);
        tv.setRichText(false);
        tv.setDrawsBackground(true);
        tv.setBackgroundColor(&NSColor::controlBackgroundColor());
        tv.setTextColor(Some(&NSColor::labelColor()));
        let font = NSFont::monospacedSystemFontOfSize_weight(
            12.0,
            objc2_app_kit::NSFontWeightRegular,
        );
        tv.setFont(Some(&font));
        tv.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        tv
    }
}

// ---------------------------------------------------------------------------
// Menubar
// ---------------------------------------------------------------------------

fn install_menubar(app: &NSApplication, mtm: MainThreadMarker) {
    let menubar: Retained<NSMenu> = NSMenu::new(mtm);
    let app_item: Retained<NSMenuItem> = NSMenuItem::new(mtm);
    menubar.addItem(&app_item);

    let app_menu: Retained<NSMenu> = NSMenu::new(mtm);
    app_item.setSubmenu(Some(&app_menu));

    let quit_title = NSString::from_str("Quit Loom");
    let quit_key = NSString::from_str("q");
    let quit_item = unsafe {
        let alloc = mtm.alloc::<NSMenuItem>();
        NSMenuItem::initWithTitle_action_keyEquivalent(
            alloc,
            &quit_title,
            Some(sel!(terminate:)),
            &quit_key,
        )
    };
    app_menu.addItem(&quit_item);

    app.setMainMenu(Some(&menubar));
}

// ---------------------------------------------------------------------------
// Refresh pipeline
// ---------------------------------------------------------------------------

fn refresh_views(bridge: &EngineBridge, views: &Views) {
    let snapshot = bridge.snapshot();
    let sidebar_text = render::render_sidebar(&snapshot);
    let content_text = render::render_content(&snapshot);
    unsafe {
        views.sidebar_tv.setString(&NSString::from_str(&sidebar_text));
        views.content_tv.setString(&NSString::from_str(&content_text));
    }
}

/// Install a repeating NSTimer on the main run loop. Fires every 500ms.
fn install_refresh_timer(mtm: MainThreadMarker, bridge: EngineBridge, views: Views) {
    let target = RefreshTarget::new(mtm, bridge, views);
    unsafe {
        let _timer: Retained<NSTimer> =
            NSTimer::scheduledTimerWithTimeInterval_target_selector_userInfo_repeats(
                REFRESH_INTERVAL_SECS,
                &*target,
                sel!(tick:),
                None,
                true,
            );
    }
    // Target is kept alive by the run loop's retain of the timer, which
    // retains its target.
}

// Custom NSObject subclass that carries the engine bridge + views across
// the Obj-C boundary. `InteriorMutable` lets us allocate via the standard
// `ClassType::alloc()` path; main-thread-only safety is enforced by the
// fact that NSTimer fires on whichever run loop scheduled it (main here).
declare_class!(
    struct RefreshTarget;

    unsafe impl ClassType for RefreshTarget {
        type Super = NSObject;
        type Mutability = mutability::InteriorMutable;
        const NAME: &'static str = "LoomRefreshTarget";
    }

    impl DeclaredClass for RefreshTarget {
        type Ivars = RefreshIvars;
    }

    unsafe impl RefreshTarget {
        #[method(tick:)]
        fn tick(&self, _timer: *mut AnyObject) {
            let ivars = self.ivars();
            refresh_views(&ivars.bridge, &ivars.views);
        }
    }
);

struct RefreshIvars {
    bridge: EngineBridge,
    views: Views,
}

impl RefreshTarget {
    fn new(_mtm: MainThreadMarker, bridge: EngineBridge, views: Views) -> Retained<Self> {
        let this = Self::alloc().set_ivars(RefreshIvars { bridge, views });
        unsafe { msg_send_id![super(this), init] }
    }
}
