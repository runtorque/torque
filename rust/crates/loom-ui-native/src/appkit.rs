//! AppKit layer — native macOS window for Loom.
//!
//! Built with objc2 0.5 + objc2-app-kit 0.2. Object graph:
//!
//! ```text
//! NSApplication
//! ├─ menubar → App menu → Quit (Cmd-Q)
//! └─ NSWindow (Titled, Closable, Resizable, Miniaturizable)
//!    └─ NSSplitView (vertical)
//!       ├─ NSScrollView → NSTextView        (sidebar, read-only)
//!       └─ Content container NSView
//!          ├─ GhosttyView                   (when an agent is selected)
//!          └─ placeholder NSScrollView+TV   (when nothing is selected)
//! ```
//!
//! Live updates: an `NSTimer` fires every 500 ms on the main run loop; the
//! callback pulls a fresh snapshot from the engine and repaints the sidebar
//! text + reconciles the content container with the engine's
//! `selected_agent_id`. Each agent gets a GhosttyView cached by id — flipping
//! selection just swaps which one is the content container's subview; the
//! underlying libghostty surface + PTY stay alive so we preserve scrollback.

use std::cell::RefCell;
use std::collections::HashMap;

use anyhow::Result;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{declare_class, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSApplication, NSApplicationActivationPolicy, NSAutoresizingMaskOptions, NSBackingStoreType,
    NSBorderType, NSColor, NSFont, NSMenu, NSMenuItem, NSScrollView, NSSplitView,
    NSSplitViewDividerStyle, NSTextView, NSView, NSWindow, NSWindowStyleMask,
};
use objc2_foundation::{
    MainThreadMarker, NSObject, NSPoint, NSRect, NSSize, NSString, NSTimer,
};

use loom_server::app::AppState;

use crate::bridge::{resolve_command, resolve_cwd, EngineBridge, MatrixStateSnapshot};
use crate::ghostty_view::GhosttyView;
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
    /// Container for the active content. Exactly one child at a time: either a
    /// `GhosttyView` (agent selected) or `placeholder_scroll` (nothing
    /// selected).
    content_container: Retained<NSView>,
    /// Shown when no agent is selected.
    placeholder_scroll: Retained<NSScrollView>,
    placeholder_tv: Retained<NSTextView>,
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

    // Content container — plain NSView that hosts either a GhosttyView or the
    // placeholder. Flips based on selection on each refresh tick.
    let content_frame = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(920.0, 760.0));
    let content_container: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        let v = NSView::initWithFrame(alloc, content_frame);
        v.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        v
    };

    // Placeholder (shown when nothing is selected).
    let placeholder_scroll = make_scroll_view(mtm, content_frame);
    let placeholder_tv = make_text_view(mtm, content_frame);
    unsafe {
        placeholder_tv.setString(&NSString::from_str(render::initial_content_placeholder()));
        placeholder_scroll.setDocumentView(Some(&placeholder_tv));
    }

    unsafe {
        split.addSubview(&sidebar_scroll);
        split.addSubview(&content_container);
        window.setContentView(Some(&split));
    }

    Views {
        window,
        sidebar_tv,
        content_container,
        placeholder_scroll,
        placeholder_tv,
    }
}

fn make_scroll_view(mtm: MainThreadMarker, frame: NSRect) -> Retained<NSScrollView> {
    unsafe {
        let alloc = mtm.alloc::<NSScrollView>();
        let scroll = NSScrollView::initWithFrame(alloc, frame);
        scroll.setHasVerticalScroller(true);
        scroll.setAutohidesScrollers(true);
        scroll.setBorderType(NSBorderType::NSNoBorder);
        scroll.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
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

/// Holds per-window UI state that mutates across refresh ticks:
/// which agent's GhosttyView is currently mounted, the cache of
/// `agent_id → GhosttyView`, and the pending-text receivers that the engine
/// pushes dispatch prompts into.
struct ContentState {
    current_mounted: Option<String>,
    cache: HashMap<String, CachedAgent>,
    /// True the first tick — forces us to mount whatever matches selection
    /// (including placeholder) even if `current_mounted == selection` (both
    /// None at init).
    first_tick: bool,
}

struct CachedAgent {
    view: Retained<GhosttyView>,
    /// Pending text from `dispatch_task` / `send_text` / `broadcast_to_group`.
    /// Drained each tick and forwarded to the surface via `send_text`.
    rx: tokio::sync::mpsc::UnboundedReceiver<String>,
}

impl ContentState {
    fn new() -> Self {
        Self {
            current_mounted: None,
            cache: HashMap::new(),
            first_tick: true,
        }
    }
}

fn tick(mtm: MainThreadMarker, bridge: &EngineBridge, views: &Views, state: &RefCell<ContentState>) {
    let snapshot = bridge.snapshot();

    // 1. Sidebar text.
    let sidebar_text = render::render_sidebar(&snapshot);
    unsafe { views.sidebar_tv.setString(&NSString::from_str(&sidebar_text)) };

    // 2. Reconcile content container against snapshot.selected_agent_id.
    reconcile_content(mtm, bridge, views, &snapshot, state);

    // 3. Drain any pending dispatch text into the corresponding GhosttyView.
    drain_pending_text(state);
}

fn drain_pending_text(state: &RefCell<ContentState>) {
    use tokio::sync::mpsc::error::TryRecvError;
    let mut st = state.borrow_mut();
    for cached in st.cache.values_mut() {
        loop {
            match cached.rx.try_recv() {
                Ok(text) => cached.view.send_text(&text),
                Err(TryRecvError::Empty) | Err(TryRecvError::Disconnected) => break,
            }
        }
    }
}

fn reconcile_content(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    views: &Views,
    snapshot: &MatrixStateSnapshot,
    state: &RefCell<ContentState>,
) {
    let mut st = state.borrow_mut();

    // Drop cached views for agents that no longer exist; unregister them so
    // dispatch stops trying to route through a dead channel.
    let live_ids: std::collections::HashSet<&str> =
        snapshot.agents.iter().map(|a| a.id.as_str()).collect();
    let dead: Vec<String> = st
        .cache
        .keys()
        .filter(|id| !live_ids.contains(id.as_str()))
        .cloned()
        .collect();
    for id in &dead {
        bridge.ui_agents().unregister(id);
        st.cache.remove(id);
    }

    let desired: Option<String> = snapshot
        .selected_agent_id
        .as_ref()
        .filter(|id| snapshot.agents.iter().any(|a| &a.id == *id))
        .cloned();

    if !st.first_tick && st.current_mounted == desired {
        return;
    }
    st.first_tick = false;

    // Remove whatever's currently mounted in the container.
    remove_container_children(&views.content_container);

    match &desired {
        Some(agent_id) => {
            // Get-or-create GhosttyView for this agent.
            if !st.cache.contains_key(agent_id) {
                let Some(agent) = snapshot.find_agent(agent_id) else {
                    // race: snapshot changed between the lookup above and
                    // here. Show placeholder and bail.
                    mount_placeholder(views);
                    st.current_mounted = None;
                    return;
                };
                let command = resolve_command(agent, &snapshot.global_default_command);
                let cwd = resolve_cwd(agent);
                let frame = container_frame(&views.content_container);
                let gv = GhosttyView::new(mtm, frame, command, cwd);
                unsafe {
                    gv.setAutoresizingMask(
                        NSAutoresizingMaskOptions::NSViewWidthSizable
                            | NSAutoresizingMaskOptions::NSViewHeightSizable,
                    );
                }
                // Register with the engine so dispatch routes prompts here.
                let rx = bridge.ui_agents().register(agent_id.clone());
                st.cache
                    .insert(agent_id.clone(), CachedAgent { view: gv, rx });
            }
            let cached = st.cache.get(agent_id).unwrap();
            let gv_as_view: &NSView = &**cached.view;
            unsafe {
                gv_as_view.setFrame(container_frame(&views.content_container));
                views.content_container.addSubview(gv_as_view);
                // Make the surface key — typing into the terminal just works.
                let _ = views.window.makeFirstResponder(Some(gv_as_view));
            }
        }
        None => mount_placeholder(views),
    }

    st.current_mounted = desired;
}

fn mount_placeholder(views: &Views) {
    unsafe {
        views.placeholder_scroll.setFrame(container_frame(&views.content_container));
        views
            .content_container
            .addSubview(&views.placeholder_scroll);
        // Refresh the placeholder text on every remount — cheap + keeps it
        // accurate if the hint text ever changes.
        views
            .placeholder_tv
            .setString(&NSString::from_str(render::initial_content_placeholder()));
    }
}

fn container_frame(container: &NSView) -> NSRect {
    let bounds = container.bounds();
    NSRect::new(NSPoint::new(0.0, 0.0), bounds.size)
}

fn remove_container_children(container: &NSView) {
    unsafe {
        let subviews = container.subviews();
        let count = subviews.count();
        for i in 0..count {
            let v = subviews.objectAtIndex(i);
            v.removeFromSuperview();
        }
    }
}

/// Install a repeating NSTimer on the main run loop. Fires every 500ms.
fn install_refresh_timer(mtm: MainThreadMarker, bridge: EngineBridge, views: Views) {
    let target = RefreshTarget::new(bridge, views);
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
    let _ = mtm; // captured to prove main-thread provenance; unused directly
    // Target is kept alive by the run loop's retain of the timer, which
    // retains its target.
}

// Custom NSObject subclass that carries the engine bridge + views across
// the Obj-C boundary. MainThreadOnly — NSTimer scheduled on the main run loop
// always fires on main, and we touch AppKit state during tick.
declare_class!(
    struct RefreshTarget;

    unsafe impl ClassType for RefreshTarget {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomRefreshTarget";
    }

    impl DeclaredClass for RefreshTarget {
        type Ivars = RefreshIvars;
    }

    unsafe impl RefreshTarget {
        #[method(tick:)]
        fn tick_method(&self, _timer: *mut AnyObject) {
            let ivars = self.ivars();
            // Safe: NSTimer fires on the main run loop; MainThreadMarker here
            // proves we're on main so we can call into AppKit APIs.
            let mtm = MainThreadMarker::from(self);
            tick(mtm, &ivars.bridge, &ivars.views, &ivars.state);
        }
    }
);

struct RefreshIvars {
    bridge: EngineBridge,
    views: Views,
    state: RefCell<ContentState>,
}

impl RefreshTarget {
    fn new(bridge: EngineBridge, views: Views) -> Retained<Self> {
        let mtm = MainThreadMarker::new()
            .expect("RefreshTarget::new must be called on the main thread");
        let ivars = RefreshIvars {
            bridge,
            views,
            state: RefCell::new(ContentState::new()),
        };
        let this = mtm.alloc::<Self>().set_ivars(ivars);
        unsafe { msg_send_id![super(this), init] }
    }
}
