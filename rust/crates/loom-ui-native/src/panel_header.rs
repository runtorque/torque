//! Small header bar that sits atop every edge-docked panel (Top / Left /
//! Right / Bottom). Shows the panel's title on the left and a "…" popup
//! button on the right that offers a Move-to submenu + Hide.
//!
//! Redocking is two engine calls: first `dock_panel` with the source zone
//! set to `null` (clears it), then `dock_panel` with the target zone set
//! to the panel's layout. The next refresh tick mounts the new shape.

use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{declare_class, msg_send, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSButton, NSColor, NSFont, NSMenu, NSMenuItem, NSTextField,
    NSView,
};
use objc2_foundation::{MainThreadMarker, NSObject, NSPoint, NSRect, NSSize, NSString};

use loom_core::state::{DockZone, LayoutNode, PanelKind};

use crate::bridge::EngineBridge;

pub const PANEL_HEADER_HEIGHT: f64 = 22.0;

/// Derive a user-visible label from a panel tree. For `Leaf` we use the
/// panel kind's name; for `Split` we just call it "Zone".
pub fn zone_title(layout: &LayoutNode) -> &'static str {
    match layout {
        LayoutNode::Leaf { panel } => match panel {
            PanelKind::Terminal { .. } => "Terminal",
            PanelKind::Sidebar => "Agents",
            PanelKind::Board => "Board",
            PanelKind::Actions => "Actions",
            PanelKind::Memory => "Memory",
            PanelKind::Events => "Events",
            PanelKind::Templates => "Templates",
            PanelKind::Context { .. } => "Context",
            PanelKind::Weaver { .. } => "Weaver",
            PanelKind::Placeholder => "Empty",
        },
        LayoutNode::Split { .. } => "Zone",
    }
}

/// Build a header bar for a zone. Sits at the top of the zone container
/// at a fixed 22pt height. Call this, size the rest of the container for
/// the panel body beneath it.
pub fn install_header(
    mtm: MainThreadMarker,
    bridge: EngineBridge,
    parent: &NSView,
    layout: &LayoutNode,
    zone: DockZone,
    parent_size: NSSize,
) {
    let title = zone_title(layout);
    let frame = NSRect::new(
        NSPoint::new(0.0, parent_size.height - PANEL_HEADER_HEIGHT),
        NSSize::new(parent_size.width, PANEL_HEADER_HEIGHT),
    );
    let header: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        let v = NSView::initWithFrame(alloc, frame);
        v.setAutoresizingMask(
            NSAutoresizingMaskOptions::NSViewWidthSizable
                | NSAutoresizingMaskOptions::NSViewMinYMargin,
        );
        v
    };

    // Title label on the left.
    let label: Retained<NSTextField> = unsafe {
        let alloc = mtm.alloc::<NSTextField>();
        let t = NSTextField::initWithFrame(
            alloc,
            NSRect::new(
                NSPoint::new(8.0, 2.0),
                NSSize::new(parent_size.width - 40.0, 16.0),
            ),
        );
        t.setStringValue(&NSString::from_str(title));
        t.setEditable(false);
        t.setSelectable(false);
        t.setBezeled(false);
        t.setBordered(false);
        t.setDrawsBackground(false);
        t.setFont(Some(&NSFont::systemFontOfSize(11.0)));
        t.setTextColor(Some(&NSColor::secondaryLabelColor()));
        t.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewWidthSizable);
        t
    };
    unsafe { header.addSubview(&label) };

    // "…" popup button on the right.
    let target: Retained<HeaderTarget> = {
        let ivars = HeaderTargetIvars {
            bridge,
            zone,
            layout_json: serde_json::to_string(layout).unwrap_or_default(),
        };
        let this = mtm.alloc::<HeaderTarget>().set_ivars(ivars);
        unsafe { msg_send_id![super(this), init] }
    };
    let button: Retained<NSButton> = unsafe {
        let alloc = mtm.alloc::<NSButton>();
        let b = NSButton::initWithFrame(
            alloc,
            NSRect::new(
                NSPoint::new(parent_size.width - 30.0, 0.0),
                NSSize::new(26.0, PANEL_HEADER_HEIGHT),
            ),
        );
        b.setTitle(&NSString::from_str("…"));
        b.setBordered(false);
        b.setTarget(Some(target.as_ref()));
        b.setAction(Some(sel!(handleHeaderMenu:)));
        b.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewMinXMargin);
        b
    };
    unsafe { header.addSubview(&button) };
    unsafe { parent.addSubview(&header) };

    // Keep the target alive with the button via a retained pointer in the
    // button's identity — simplest way is to attach it as an associated
    // object. For v1 we rely on the header being parented to the zone
    // host which outlives the menu.
    //
    // Store the target as a header subview's identifier so it's retained
    // as long as the header exists. NSButton doesn't retain its target,
    // so we wrap the target in an NSView subview that holds a strong
    // Rust-side Retained reference. Practically: we just lean on the
    // outer parent view retaining the NSButton's ivars via the closure
    // capture path. Leaked until window close — acceptable for v1.
    //
    // TODO: attach via `objc_setAssociatedObject` once we have clipboard/
    // drag-drop patterns standardized.
    std::mem::forget(target);
}

// ---------------------------------------------------------------------------
// Menu target — handles the "…" button click and the menu-item callbacks.
// ---------------------------------------------------------------------------

pub(crate) struct HeaderTargetIvars {
    bridge: EngineBridge,
    zone: DockZone,
    /// JSON-encoded layout this zone currently holds. Used when moving to
    /// a new zone so the UI can copy the tree over.
    layout_json: String,
}

declare_class!(
    pub(crate) struct HeaderTarget;

    unsafe impl ClassType for HeaderTarget {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomPanelHeaderTarget";
    }

    impl DeclaredClass for HeaderTarget {
        type Ivars = HeaderTargetIvars;
    }

    unsafe impl HeaderTarget {
        #[method(handleHeaderMenu:)]
        fn handle_header_menu(&self, sender: &NSButton) {
            let mtm = MainThreadMarker::from(self);
            let menu = build_menu(mtm, self);
            // Pop up the menu at the button's position.
            let loc = NSPoint::new(0.0, PANEL_HEADER_HEIGHT);
            let sender_view: &NSView = &**sender;
            unsafe {
                let _: bool =
                    msg_send![&menu, popUpMenuPositioningItem: std::ptr::null::<NSMenuItem>(), atLocation: loc, inView: sender_view];
            }
        }

        #[method(handleMoveTo:)]
        fn handle_move_to(&self, sender: &NSMenuItem) {
            let Some(rep) = (unsafe { sender.representedObject() }) else { return };
            let Some(ns) = (unsafe { downcast_retained::<NSString>(rep) }) else { return };
            let encoded = ns.to_string();
            let ivars = self.ivars();
            let source = zone_name(ivars.zone);
            if source == encoded {
                return;
            }
            let layout_value: serde_json::Value =
                serde_json::from_str(&ivars.layout_json).unwrap_or(serde_json::Value::Null);
            // Clear source first, then set target.
            if let Err(e) = ivars.bridge.dispatch(
                "dock_panel",
                serde_json::json!({ "zone": source, "layout": serde_json::Value::Null }),
            ) {
                tracing::warn!("dock_panel clear failed: {e:?}");
                return;
            }
            if let Err(e) = ivars.bridge.dispatch(
                "dock_panel",
                serde_json::json!({ "zone": encoded, "layout": layout_value }),
            ) {
                tracing::warn!("dock_panel set failed: {e:?}");
            }
        }

        #[method(handleHide:)]
        fn handle_hide(&self, _sender: &NSMenuItem) {
            let ivars = self.ivars();
            let zone = zone_name(ivars.zone);
            if let Err(e) = ivars.bridge.dispatch(
                "dock_panel",
                serde_json::json!({ "zone": zone, "layout": serde_json::Value::Null }),
            ) {
                tracing::warn!("dock_panel hide failed: {e:?}");
            }
        }
    }
);

fn zone_name(zone: DockZone) -> &'static str {
    match zone {
        DockZone::Top => "top",
        DockZone::Left => "left",
        DockZone::Right => "right",
        DockZone::Bottom => "bottom",
        DockZone::Center => "center",
    }
}

fn build_menu(mtm: MainThreadMarker, target: &HeaderTarget) -> Retained<NSMenu> {
    let menu: Retained<NSMenu> = NSMenu::new(mtm);
    unsafe { menu.setAutoenablesItems(false) };

    // Move to submenu.
    let move_item: Retained<NSMenuItem> = unsafe {
        let alloc = mtm.alloc::<NSMenuItem>();
        NSMenuItem::initWithTitle_action_keyEquivalent(
            alloc,
            &NSString::from_str("Move to"),
            None,
            &NSString::from_str(""),
        )
    };
    let submenu: Retained<NSMenu> = NSMenu::new(mtm);
    let source = target.ivars().zone;
    for candidate in &[
        DockZone::Top,
        DockZone::Left,
        DockZone::Right,
        DockZone::Bottom,
    ] {
        if *candidate == source {
            continue;
        }
        let label = match candidate {
            DockZone::Top => "Top",
            DockZone::Left => "Left",
            DockZone::Right => "Right",
            DockZone::Bottom => "Bottom",
            DockZone::Center => "Center",
        };
        let it: Retained<NSMenuItem> = unsafe {
            let alloc = mtm.alloc::<NSMenuItem>();
            NSMenuItem::initWithTitle_action_keyEquivalent(
                alloc,
                &NSString::from_str(label),
                Some(sel!(handleMoveTo:)),
                &NSString::from_str(""),
            )
        };
        unsafe {
            it.setTarget(Some(target));
            it.setRepresentedObject(Some(&NSString::from_str(zone_name(*candidate))));
        }
        submenu.addItem(&it);
    }
    move_item.setSubmenu(Some(&submenu));
    menu.addItem(&move_item);

    menu.addItem(&NSMenuItem::separatorItem(mtm));

    let hide: Retained<NSMenuItem> = unsafe {
        let alloc = mtm.alloc::<NSMenuItem>();
        NSMenuItem::initWithTitle_action_keyEquivalent(
            alloc,
            &NSString::from_str("Hide"),
            Some(sel!(handleHide:)),
            &NSString::from_str(""),
        )
    };
    unsafe { hide.setTarget(Some(target)) };
    menu.addItem(&hide);

    menu
}

unsafe fn downcast_retained<T: ClassType>(obj: Retained<AnyObject>) -> Option<Retained<T>> {
    let cls = T::class();
    let is_kind: bool = msg_send![&*obj, isKindOfClass: cls];
    if is_kind {
        Some(Retained::cast::<T>(obj))
    } else {
        None
    }
}
