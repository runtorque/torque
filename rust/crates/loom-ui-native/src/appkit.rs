//! AppKit layer — native macOS window for Loom.
//!
//! Built with objc2 0.5 + objc2-app-kit 0.2. Object graph:
//!
//! ```text
//! NSApplication
//! ├─ menubar → App menu → Quit (Cmd-Q)
//! └─ NSWindow (Titled, Closable, Resizable, Miniaturizable)
//!    └─ outer NSView (dock-layout container)
//!       └─ NSSplitView (vertical layout, horizontal dividers)
//!          ├─ Top zone        (optional)
//!          ├─ Middle NSSplitView (horizontal layout, vertical dividers)
//!          │  ├─ Left zone    (Sidebar by default)
//!          │  ├─ Center zone  (LayoutNode tree: terminals + nested panels)
//!          │  └─ Right zone   (optional)
//!          └─ Bottom zone     (Board by default)
//! ```
//!
//! Each zone's contents are a `LayoutNode` subtree mounted via
//! `mount_panel_into`. Terminal leaves host libghostty surfaces; panel
//! leaves (Sidebar / Board / ...) host their dedicated native renderers.
//!
//! Live updates: an `NSTimer` fires every 500 ms on the main run loop; the
//! callback pulls a fresh snapshot from the engine, reconciles the dock
//! layout (rebuilds NSSplitView tree only when its JSON signature changed),
//! and asks each cached panel renderer to self-refresh. Cached GhosttyView
//! + SidebarView + BoardView instances survive layout rebuilds so their
//! PTYs / expansion state / scroll position are preserved.

use std::cell::RefCell;
use std::collections::HashMap;

use anyhow::Result;
use loom_core::state::{DockLayout, LayoutNode, PanelKind, SplitAxis};
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{declare_class, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSApplication, NSApplicationActivationPolicy, NSAutoresizingMaskOptions, NSBackingStoreType,
    NSBorderType, NSColor, NSFont, NSMenu, NSMenuItem, NSScrollView, NSSplitView,
    NSSplitViewDividerStyle, NSTextView, NSView, NSWindow, NSWindowStyleMask,
};
use objc2_foundation::{MainThreadMarker, NSObject, NSPoint, NSRect, NSSize, NSString, NSTimer};

use loom_server::app::{AppState, UiAgentInput};

use crate::board::BoardView;
use crate::bridge::{resolve_command, resolve_cwd, EngineBridge, MatrixStateSnapshot};
use crate::ghostty_view::GhosttyView;
use crate::panel_header::{install_header, PANEL_HEADER_HEIGHT};
use crate::render;
use crate::sidebar::SidebarView;

use loom_core::state::DockZone;

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
    /// Outer container hosting the full dock layout. Reconciliation rebuilds
    /// its NSSplitView subtree when the dock signature changes; cached
    /// panel views (sidebar / board / per-agent GhosttyView) in
    /// `ContentState` survive across rebuilds.
    outer: Retained<NSView>,
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

    let outer: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        let v = NSView::initWithFrame(
            alloc,
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(1200.0, 760.0)),
        );
        v.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        v
    };
    window.setContentView(Some(&outer));

    Views { window, outer }
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
        let font =
            NSFont::monospacedSystemFontOfSize_weight(12.0, objc2_app_kit::NSFontWeightRegular);
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
/// the cache of `agent_id → GhosttyView` (preserves PTYs across layout
/// rebuilds), cached sidebar + board renderers, and a signature of the
/// currently-mounted dock layout so we only rebuild the NSSplitView tree
/// when it actually changed.
struct ContentState {
    /// JSON signature of the currently-mounted dock layout + selection.
    /// Next tick's computed signature is compared; on mismatch we wipe +
    /// rebuild.
    current_signature: Option<String>,
    cache: HashMap<String, CachedAgent>,
    /// Reusable native panel renderers. Panels are created lazily when the
    /// dock layout first mounts them, then reused across layout rebuilds
    /// so their in-memory state (outline expansion, board scroll, etc.)
    /// is preserved.
    sidebar_cache: Option<SidebarView>,
    board_cache: Option<BoardView>,
    /// True the first tick — forces an initial mount even if the layout
    /// signature happens to match `None`.
    first_tick: bool,
}

struct CachedAgent {
    view: Retained<GhosttyView>,
    /// Pending text from `dispatch_task` / `send_text` / `broadcast_to_group`.
    /// Drained each tick and forwarded to the surface as text or submit.
    rx: tokio::sync::mpsc::UnboundedReceiver<UiAgentInput>,
}

impl ContentState {
    fn new() -> Self {
        Self {
            current_signature: None,
            cache: HashMap::new(),
            sidebar_cache: None,
            board_cache: None,
            first_tick: true,
        }
    }
}

/// Compact textual signature for the dock layout — used to skip rebuilds
/// when the snapshot's tree hasn't actually changed. We include
/// `selected_agent_id` because `Terminal { id: None }` panes bind to it,
/// so a selection change affects which view a leaf renders.
fn dock_signature(dock: &DockLayout, selected: &Option<String>) -> String {
    serde_json::to_string(&serde_json::json!({
        "dock": dock,
        "selected": selected,
    }))
    .unwrap_or_default()
}

fn tick(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    views: &Views,
    state: &RefCell<ContentState>,
) {
    let snapshot = bridge.snapshot();

    // 1. Reconcile the outer dock layout — rebuild NSSplitView tree only
    //    when the dock signature changed. Cached panel instances survive.
    reconcile_dock(mtm, bridge, views, &snapshot, state);

    // 2. Drain any pending dispatch text into the corresponding GhosttyView.
    drain_pending_text(state);

    // 3. Tell each cached panel renderer to refresh itself. They short-
    //    circuit if their own content hasn't changed.
    let (sidebar_opt, board_opt) = {
        let st = state.borrow();
        (st.sidebar_cache.clone(), st.board_cache.clone())
    };
    if let Some(s) = sidebar_opt {
        s.reload_if_changed(&snapshot);
    }
    if let Some(b) = board_opt {
        b.reload_if_changed(&snapshot);
    }
}

fn drain_pending_text(state: &RefCell<ContentState>) {
    use tokio::sync::mpsc::error::TryRecvError;
    let mut st = state.borrow_mut();
    for cached in st.cache.values_mut() {
        loop {
            match cached.rx.try_recv() {
                Ok(UiAgentInput::Text(text)) => cached.view.send_text(&text),
                Ok(UiAgentInput::Submit) => cached.view.submit(),
                Err(TryRecvError::Empty) | Err(TryRecvError::Disconnected) => break,
            }
        }
    }
}

fn reconcile_dock(
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

    // Skip rebuild when the dock layout (+ dependent selection) hasn't changed.
    let sig = dock_signature(&snapshot.dock_layout, &snapshot.selected_agent_id);
    if !st.first_tick && st.current_signature.as_deref() == Some(sig.as_str()) {
        return;
    }
    st.first_tick = false;
    st.current_signature = Some(sig);

    // Wipe the outer container and rebuild from the dock layout. Cached
    // panel/terminal instances are reused.
    remove_container_children(&views.outer);
    build_dock_into(mtm, bridge, snapshot, &views.outer, &mut *st);

    // Focus the selected agent's terminal, if it ended up in the tree.
    if let Some(sel) = &snapshot.selected_agent_id {
        if let Some(cached) = st.cache.get(sel) {
            let gv: &NSView = &**cached.view;
            let _ = views.window.makeFirstResponder(Some(gv));
        }
    }
}

/// Build the full dock layout (cross of top / left / center / right /
/// bottom zones) into `parent`. Hidden edges (None) are omitted from the
/// split views entirely; present edges get a subview in the relevant
/// dimension.
fn build_dock_into(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    snapshot: &MatrixStateSnapshot,
    parent: &NSView,
    state: &mut ContentState,
) {
    let dock = &snapshot.dock_layout;
    let ratios = dock.ratios;

    // Outer split: vertical layout (horizontal dividers) for top/middle/bottom.
    let outer = make_split(mtm, parent, SplitAxis::Vertical);
    unsafe { parent.addSubview(&outer) };

    let parent_bounds = parent.bounds();
    let total_h = parent_bounds.size.height.max(1.0);

    // Top zone (if present).
    let mut outer_order: Vec<(f64, Retained<NSView>)> = Vec::new();
    if let Some(top_layout) = &dock.top {
        let host = make_pane_host(mtm);
        unsafe { outer.addSubview(&host) };
        mount_edge_zone_into(
            mtm,
            bridge,
            snapshot,
            top_layout,
            DockZone::Top,
            &host,
            state,
        );
        outer_order.push((ratios.top * total_h, host));
    }

    // Middle row — left / center / right — always present.
    let middle_host = make_pane_host(mtm);
    unsafe { outer.addSubview(&middle_host) };
    build_middle_row(mtm, bridge, snapshot, dock, &middle_host, state);
    outer_order.push((f64::NAN, middle_host));

    // Bottom zone (if present).
    if let Some(bottom_layout) = &dock.bottom {
        let host = make_pane_host(mtm);
        unsafe { outer.addSubview(&host) };
        mount_edge_zone_into(
            mtm,
            bridge,
            snapshot,
            bottom_layout,
            DockZone::Bottom,
            &host,
            state,
        );
        outer_order.push((ratios.bottom * total_h, host));
    }

    apply_outer_ratios(&outer, total_h, &outer_order);
}

fn build_middle_row(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    snapshot: &MatrixStateSnapshot,
    dock: &DockLayout,
    parent: &NSView,
    state: &mut ContentState,
) {
    let row = make_split(mtm, parent, SplitAxis::Horizontal);
    unsafe { parent.addSubview(&row) };

    let parent_bounds = parent.bounds();
    let total_w = parent_bounds.size.width.max(1.0);
    let ratios = dock.ratios;

    let mut order: Vec<(f64, Retained<NSView>)> = Vec::new();

    if let Some(left) = &dock.left {
        let host = make_pane_host(mtm);
        unsafe { row.addSubview(&host) };
        mount_edge_zone_into(mtm, bridge, snapshot, left, DockZone::Left, &host, state);
        order.push((ratios.left * total_w, host));
    }

    // Center is always present — no header bar (it's the main workspace).
    let center_host = make_pane_host(mtm);
    unsafe { row.addSubview(&center_host) };
    build_layout_into(mtm, bridge, snapshot, &dock.center, &center_host, state);
    order.push((f64::NAN, center_host));

    if let Some(right) = &dock.right {
        let host = make_pane_host(mtm);
        unsafe { row.addSubview(&host) };
        mount_edge_zone_into(mtm, bridge, snapshot, right, DockZone::Right, &host, state);
        order.push((ratios.right * total_w, host));
    }

    apply_row_ratios(&row, total_w, &order);
}

/// Mount a panel tree inside an edge zone. Splits the zone vertically into
/// a 22pt header bar on top + the panel content below; the header carries
/// the Move-to / Hide menu.
fn mount_edge_zone_into(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    snapshot: &MatrixStateSnapshot,
    layout: &LayoutNode,
    zone: DockZone,
    parent: &NSView,
    state: &mut ContentState,
) {
    let bounds = parent.bounds();
    let parent_size = bounds.size;
    install_header(mtm, bridge.clone(), parent, layout, zone, parent_size);

    // Content area fills the parent minus the header strip at the top.
    let content_h = (parent_size.height - PANEL_HEADER_HEIGHT).max(0.0);
    let content_frame = NSRect::new(
        NSPoint::new(0.0, 0.0),
        NSSize::new(parent_size.width, content_h),
    );
    let content: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        let v = NSView::initWithFrame(alloc, content_frame);
        v.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        v
    };
    unsafe { parent.addSubview(&content) };
    build_layout_into(mtm, bridge, snapshot, layout, &content, state);
}

/// Apply dividers for the outer (vertical-layout) split. `order` lists the
/// subviews top→bottom with their desired pixel sizes; NaN means "fill
/// remaining". Only the non-NaN sizes are pinned via divider positions.
fn apply_outer_ratios(split: &NSSplitView, _total: f64, order: &[(f64, Retained<NSView>)]) {
    if order.len() < 2 {
        return;
    }
    // Divider 0 between subviews 0 and 1, etc. Set pinned sizes from the
    // top for subviews that have an explicit size.
    let mut accumulated = 0.0;
    for (i, (size, _)) in order.iter().enumerate() {
        if i == order.len() - 1 {
            break;
        }
        if size.is_nan() {
            // The filler subview — skip; the next pinned size (if any) will
            // drive its boundary.
            continue;
        }
        accumulated += size;
        unsafe {
            split.setPosition_ofDividerAtIndex(accumulated, i as isize);
        }
    }
}

/// Row (horizontal-layout) version. Left-pinned widths; right pane (if
/// present) gets its divider positioned from the right edge via
/// `total - size`.
fn apply_row_ratios(split: &NSSplitView, total: f64, order: &[(f64, Retained<NSView>)]) {
    if order.len() < 2 {
        return;
    }
    // Left-pinned: divider 0 sits `left_size` from the left.
    // If there's a right zone, its divider is at `total - right_size`.
    let (left_size, _) = &order[0];
    if !left_size.is_nan() {
        unsafe {
            split.setPosition_ofDividerAtIndex(*left_size, 0);
        }
    }
    if order.len() >= 3 {
        let (right_size, _) = &order[order.len() - 1];
        if !right_size.is_nan() {
            let pos = (total - *right_size).max(0.0);
            unsafe {
                split.setPosition_ofDividerAtIndex(pos, (order.len() - 2) as isize);
            }
        }
    }
}

fn make_split(mtm: MainThreadMarker, parent: &NSView, axis: SplitAxis) -> Retained<NSSplitView> {
    unsafe {
        let alloc = mtm.alloc::<NSSplitView>();
        let split = NSSplitView::initWithFrame(alloc, container_frame(parent));
        // NSSplitView::vertical = true → the divider is vertical → panes
        // sit side by side (horizontal layout).
        split.setVertical(matches!(axis, SplitAxis::Horizontal));
        split.setDividerStyle(NSSplitViewDividerStyle::Thin);
        split.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        split
    }
}

/// Recursively assemble the layout tree as nested NSSplitViews under
/// `parent`. Each leaf becomes either a GhosttyView (for terminals) or a
/// placeholder NSScrollView (for panels not yet ported).
fn build_layout_into(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    snapshot: &MatrixStateSnapshot,
    node: &LayoutNode,
    parent: &NSView,
    state: &mut ContentState,
) {
    match node {
        LayoutNode::Leaf { panel } => {
            mount_panel_into(mtm, bridge, snapshot, panel, parent, state);
        }
        LayoutNode::Split {
            axis,
            ratio,
            first,
            second,
        } => {
            let split = make_split_for_axis(mtm, parent, *axis);
            unsafe {
                parent.addSubview(&split);
            }
            // Build the two subviews into intermediate containers — keeps the
            // recursion type-uniform (always an NSView host).
            let first_host = make_pane_host(mtm);
            let second_host = make_pane_host(mtm);
            unsafe {
                split.addSubview(&first_host);
                split.addSubview(&second_host);
            }
            build_layout_into(mtm, bridge, snapshot, first, &first_host, state);
            build_layout_into(mtm, bridge, snapshot, second, &second_host, state);

            // Apply the ratio after both panes are attached. NSSplitView
            // measures off the parent's frame; setPosition is a no-op until
            // it has bounds, so we also schedule a follow-up layout pass.
            apply_split_ratio(&split, *axis, *ratio, parent);
        }
    }
}

fn mount_panel_into(
    mtm: MainThreadMarker,
    bridge: &EngineBridge,
    snapshot: &MatrixStateSnapshot,
    panel: &PanelKind,
    parent: &NSView,
    state: &mut ContentState,
) {
    match panel {
        PanelKind::Terminal { id } => {
            // Resolve which agent this terminal renders. `None` binds to
            // `selected_agent_id`.
            let agent_id = id.clone().or_else(|| snapshot.selected_agent_id.clone());
            let Some(agent_id) = agent_id else {
                mount_placeholder_into(parent, render::initial_content_placeholder());
                return;
            };
            let Some(agent) = snapshot.find_agent(&agent_id) else {
                mount_placeholder_into(parent, &format!("agent {agent_id} not found"));
                return;
            };

            // Get-or-create the GhosttyView for this agent + register the
            // dispatch channel with the engine.
            if !state.cache.contains_key(&agent_id) {
                let command = resolve_command(agent, &snapshot.global_default_command);
                let cwd = resolve_cwd(agent);
                let frame = container_frame(parent);
                let gv = GhosttyView::new(mtm, frame, command, cwd);
                unsafe {
                    gv.setAutoresizingMask(
                        NSAutoresizingMaskOptions::NSViewWidthSizable
                            | NSAutoresizingMaskOptions::NSViewHeightSizable,
                    );
                }
                let rx = bridge.ui_agents().register(agent_id.clone());
                state
                    .cache
                    .insert(agent_id.clone(), CachedAgent { view: gv, rx });
            }
            let cached = state.cache.get(&agent_id).unwrap();
            let gv: &NSView = &**cached.view;
            unsafe {
                gv.setFrame(container_frame(parent));
                parent.addSubview(gv);
            }
        }
        PanelKind::Placeholder => mount_placeholder_into(parent, "Empty pane"),
        PanelKind::Sidebar => {
            // Get-or-create the sidebar panel. Cached across dock rebuilds
            // so expansion + scroll survive.
            if state.sidebar_cache.is_none() {
                state.sidebar_cache = Some(SidebarView::install(mtm, bridge.clone()));
            }
            let sb = state.sidebar_cache.as_ref().unwrap();
            let frame = container_frame(parent);
            let view: &NSView = &sb.container;
            unsafe {
                view.setFrame(frame);
                parent.addSubview(view);
            }
            // First paint — blank tree until the next tick reloads.
            sb.reload_if_changed(snapshot);
        }
        PanelKind::Board => {
            if state.board_cache.is_none() {
                state.board_cache = Some(BoardView::install(mtm, bridge.clone()));
            }
            let b = state.board_cache.as_ref().unwrap();
            let frame = container_frame(parent);
            let view: &NSView = &b.container;
            unsafe {
                view.setFrame(frame);
                parent.addSubview(view);
            }
            b.reload_if_changed(snapshot);
        }
        PanelKind::Actions => mount_placeholder_into(parent, "[Actions panel — pending native UI]"),
        PanelKind::Memory => mount_placeholder_into(parent, "[Memory panel — pending native UI]"),
        PanelKind::Events => mount_placeholder_into(parent, "[Events panel — pending native UI]"),
        PanelKind::Templates => {
            mount_placeholder_into(parent, "[Templates panel — pending native UI]")
        }
        PanelKind::Context { agent_id } => {
            let label = match agent_id {
                Some(id) => format!("[Context: agent {id} — pending native UI]"),
                None => "[Context — pending native UI]".to_string(),
            };
            mount_placeholder_into(parent, &label);
        }
        PanelKind::Weaver { group } => {
            let label = match group {
                Some(g) => format!("[Weaver: {g} — pending native UI]"),
                None => "[Weaver — pending native UI]".to_string(),
            };
            mount_placeholder_into(parent, &label);
        }
    }
}

fn make_pane_host(mtm: MainThreadMarker) -> Retained<NSView> {
    unsafe {
        let alloc = mtm.alloc::<NSView>();
        let v = NSView::initWithFrame(
            alloc,
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(100.0, 100.0)),
        );
        v.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        v
    }
}

fn make_split_for_axis(
    mtm: MainThreadMarker,
    parent: &NSView,
    axis: SplitAxis,
) -> Retained<NSSplitView> {
    unsafe {
        let alloc = mtm.alloc::<NSSplitView>();
        let split = NSSplitView::initWithFrame(alloc, container_frame(parent));
        // NSSplitView::vertical = true means the *divider* is vertical (panes
        // sit side by side, i.e. horizontal layout).
        split.setVertical(matches!(axis, SplitAxis::Horizontal));
        split.setDividerStyle(NSSplitViewDividerStyle::Thin);
        split.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewHeightSizable,
        );
        split
    }
}

fn apply_split_ratio(split: &NSSplitView, axis: SplitAxis, ratio: f64, parent: &NSView) {
    let bounds = parent.bounds();
    let total = match axis {
        SplitAxis::Horizontal => bounds.size.width,
        SplitAxis::Vertical => bounds.size.height,
    };
    if total <= 0.0 {
        // No bounds yet — let NSSplitView default to even split until the
        // next resize tick.
        return;
    }
    let r = ratio.clamp(0.05, 0.95);
    let pos = total * r;
    unsafe {
        split.setPosition_ofDividerAtIndex(pos, 0);
    }
}

/// Mount a placeholder NSScrollView+NSTextView with the given hint into
/// `parent`. Used both for the no-selection state and for panel kinds whose
/// native UI hasn't been built yet.
fn mount_placeholder_into(parent: &NSView, hint: &str) {
    let mtm = MainThreadMarker::new().expect("placeholder mounting must be called on main thread");
    let frame = container_frame(parent);
    let scroll = make_scroll_view(mtm, frame);
    let tv = make_text_view(mtm, frame);
    unsafe {
        tv.setString(&NSString::from_str(hint));
        scroll.setDocumentView(Some(&tv));
        scroll.setFrame(frame);
        parent.addSubview(&scroll);
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
        let mtm =
            MainThreadMarker::new().expect("RefreshTarget::new must be called on the main thread");
        let ivars = RefreshIvars {
            bridge,
            views,
            state: RefCell::new(ContentState::new()),
        };
        let this = mtm.alloc::<Self>().set_ivars(ivars);
        unsafe { msg_send_id![super(this), init] }
    }
}
