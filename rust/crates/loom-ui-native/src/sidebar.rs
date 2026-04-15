//! Native sidebar: `NSOutlineView` backed by the engine snapshot.
//!
//! Replaces the prior NSTextView dump. Rows are clickable, keyboard-navigable
//! (default NSOutlineView behavior), and expose a right-click context menu for
//! Rename / Edit / Relaunch / Remove / Add-Terminal operations. A "+ Add Group"
//! button sits above the outline.
//!
//! Data flow:
//!
//! 1. [`SidebarView::install`] builds an NSScrollView-hosted NSOutlineView +
//!    button container and returns a handle.
//! 2. [`SidebarView::reload_if_changed`] is called on every refresh tick. It
//!    rebuilds a typed [`SidebarTree`] via [`render::build_sidebar_tree`],
//!    hashes the JSON signature, and only reloads the outline when the tree
//!    actually changed. Stable SidebarItem identities are preserved in a
//!    pool so expansion + selection survive reloads.
//! 3. Clicking a row dispatches `select_agent` through the engine — the UI
//!    does NOT update its selection locally; the next tick's snapshot brings
//!    the confirmed selection back.
//! 4. Context-menu items encode `<action>|<id>` in `NSMenuItem.representedObject`
//!    and route to a single `handleMenuAction:` on a shared target NSObject.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObjectProtocol, ProtocolObject};
use objc2::{declare_class, msg_send, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSBorderType, NSButton, NSColor, NSControlTextEditingDelegate,
    NSEvent, NSFont, NSMenu, NSMenuItem, NSOutlineView, NSOutlineViewDataSource,
    NSOutlineViewDelegate, NSScrollView, NSTableColumn, NSTextField, NSView,
};
use objc2_foundation::{
    MainThreadMarker, NSIndexSet, NSNotification, NSObject, NSPoint, NSRect, NSSize, NSString,
};

use crate::bridge::EngineBridge;
use crate::modal::{confirm, prompt_form, FormField};
use crate::render::{self, SidebarNode, SidebarTree};

const ITEM_KIND_GROUP: u32 = 0;
const ITEM_KIND_AGENT: u32 = 1;
const ITEM_KIND_TERMINAL: u32 = 2;

// Cocoa constant: NSTableViewSelectionHighlightStyleSourceList. Declared
// here because objc2-app-kit 0.2's enum doesn't expose the variant under
// the name we'd expect; the underlying NSInteger is stable.
const NS_SELECTION_HIGHLIGHT_SOURCE_LIST: isize = 1;

// ---------------------------------------------------------------------------
// Small objc2 helpers
// ---------------------------------------------------------------------------

/// Blind-cast `Retained<AnyObject>` to a concrete type. Safe only when the
/// caller knows by construction that `obj` is an instance of `T` (for us:
/// every item in the outline is a `SidebarItem`; every notification object
/// from our outline is an `NSOutlineView`).
unsafe fn cast_retained<T: objc2::Message>(obj: Retained<AnyObject>) -> Retained<T> {
    Retained::cast::<T>(obj)
}

/// Class-checked downcast. Returns `None` if `obj`'s runtime class is not
/// `T` (or a subclass). Used on values whose type we can't prove at compile
/// time — e.g. an NSNotification's `object`.
unsafe fn downcast_retained<T: ClassType>(obj: Retained<AnyObject>) -> Option<Retained<T>> {
    let cls = T::class();
    let is_kind: bool = msg_send![&*obj, isKindOfClass: cls];
    if is_kind {
        Some(cast_retained::<T>(obj))
    } else {
        None
    }
}

/// Single-index NSIndexSet. objc2-foundation 0.2 doesn't expose a direct
/// Rust constructor so we drop to `indexSetWithIndex:` via msg_send_id.
fn index_set_with_index(idx: usize) -> Retained<NSIndexSet> {
    unsafe { msg_send_id![NSIndexSet::class(), indexSetWithIndex: idx] }
}

// ---------------------------------------------------------------------------
// SidebarItem — NSObject wrapper used as NSOutlineView row identity.
// ---------------------------------------------------------------------------
//
// NSOutlineView tracks rows by item pointer. We therefore pool SidebarItem
// instances by (key, kind) so identity survives reloads and expansion state
// stays put.

pub(crate) struct SidebarItemIvars {
    key: String,
    kind: u32,
}

declare_class!(
    pub(crate) struct SidebarItem;

    unsafe impl ClassType for SidebarItem {
        type Super = NSObject;
        type Mutability = mutability::InteriorMutable;
        const NAME: &'static str = "LoomSidebarItem";
    }

    impl DeclaredClass for SidebarItem {
        type Ivars = SidebarItemIvars;
    }
);

impl SidebarItem {
    fn new(key: String, kind: u32) -> Retained<Self> {
        let this = Self::alloc().set_ivars(SidebarItemIvars { key, kind });
        unsafe { msg_send_id![super(this), init] }
    }
    fn key(&self) -> &str {
        &self.ivars().key
    }
    fn kind(&self) -> u32 {
        self.ivars().kind
    }
}

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

struct SidebarInner {
    tree: SidebarTree,
    signature: String,
    pool: HashMap<(String, u32), Retained<SidebarItem>>,
    bridge: EngineBridge,
    /// `(group_name, Vec<SidebarNode>)` lookup for fast child resolution.
    /// Rebuilt alongside `tree` on each reload.
    group_nodes: HashMap<String, Vec<SidebarNode>>,
    /// `node_id → (parent_key, SidebarNode)` for agent/terminal lookups.
    nodes_by_id: HashMap<String, SidebarNode>,
}

impl SidebarInner {
    fn new(bridge: EngineBridge) -> Self {
        Self {
            tree: SidebarTree {
                groups: Vec::new(),
                selected_id: None,
            },
            signature: String::new(),
            pool: HashMap::new(),
            bridge,
            group_nodes: HashMap::new(),
            nodes_by_id: HashMap::new(),
        }
    }

    fn item_for(&mut self, key: String, kind: u32) -> Retained<SidebarItem> {
        self.pool
            .entry((key.clone(), kind))
            .or_insert_with(|| SidebarItem::new(key, kind))
            .clone()
    }

    /// Rebuild indexes + prune pool entries for ids that vanished.
    fn reindex(&mut self) {
        self.group_nodes.clear();
        self.nodes_by_id.clear();
        let mut live: HashSet<(String, u32)> = HashSet::new();
        for g in &self.tree.groups {
            live.insert((group_key(&g.name), ITEM_KIND_GROUP));
            self.group_nodes.insert(g.name.clone(), g.nodes.clone());
            for n in &g.nodes {
                index_node(n, &mut self.nodes_by_id, &mut live);
            }
        }
        self.pool.retain(|k, _| live.contains(k));
    }
}

fn group_key(name: &str) -> String {
    format!("g:{name}")
}

fn index_node(
    n: &SidebarNode,
    map: &mut HashMap<String, SidebarNode>,
    live: &mut HashSet<(String, u32)>,
) {
    let kind = if n.cell_type == "terminal" {
        ITEM_KIND_TERMINAL
    } else {
        ITEM_KIND_AGENT
    };
    live.insert((n.id.clone(), kind));
    map.insert(n.id.clone(), n.clone());
    for c in &n.children {
        index_node(c, map, live);
    }
}

type Shared = Rc<RefCell<SidebarInner>>;

// ---------------------------------------------------------------------------
// Data source
// ---------------------------------------------------------------------------

pub(crate) struct SidebarDataSourceIvars {
    inner: Shared,
}

declare_class!(
    pub(crate) struct SidebarDataSource;

    unsafe impl ClassType for SidebarDataSource {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomSidebarDataSource";
    }

    impl DeclaredClass for SidebarDataSource {
        type Ivars = SidebarDataSourceIvars;
    }

    unsafe impl NSObjectProtocol for SidebarDataSource {}
    unsafe impl NSOutlineViewDataSource for SidebarDataSource {}

    unsafe impl SidebarDataSource {
        #[method(outlineView:numberOfChildrenOfItem:)]
        fn number_of_children(
            &self,
            _outline: &NSOutlineView,
            item: Option<&SidebarItem>,
        ) -> isize {
            let inner = self.ivars().inner.borrow();
            match item {
                None => inner.tree.groups.len() as isize,
                Some(item) => match item.kind() {
                    ITEM_KIND_GROUP => {
                        let name = item.key().trim_start_matches("g:");
                        inner
                            .group_nodes
                            .get(name)
                            .map(|v| v.len() as isize)
                            .unwrap_or(0)
                    }
                    _ => inner
                        .nodes_by_id
                        .get(item.key())
                        .map(|n| n.children.len() as isize)
                        .unwrap_or(0),
                },
            }
        }

        #[method_id(outlineView:child:ofItem:)]
        fn child_of_item(
            &self,
            _outline: &NSOutlineView,
            index: isize,
            item: Option<&SidebarItem>,
        ) -> Retained<SidebarItem> {
            let mut inner = self.ivars().inner.borrow_mut();
            let idx = index as usize;
            match item {
                None => {
                    let name = inner.tree.groups[idx].name.clone();
                    inner.item_for(group_key(&name), ITEM_KIND_GROUP)
                }
                Some(item) => match item.kind() {
                    ITEM_KIND_GROUP => {
                        let group_name = item.key().trim_start_matches("g:").to_string();
                        let node = inner
                            .group_nodes
                            .get(&group_name)
                            .and_then(|v| v.get(idx))
                            .cloned()
                            .expect("child_of_item: group out of range");
                        let kind = node_kind(&node);
                        inner.item_for(node.id, kind)
                    }
                    _ => {
                        let parent = inner
                            .nodes_by_id
                            .get(item.key())
                            .cloned()
                            .expect("child_of_item: parent not found");
                        let child = parent
                            .children
                            .get(idx)
                            .cloned()
                            .expect("child_of_item: child out of range");
                        let kind = node_kind(&child);
                        inner.item_for(child.id, kind)
                    }
                },
            }
        }

        #[method(outlineView:isItemExpandable:)]
        fn is_expandable(&self, _outline: &NSOutlineView, item: &SidebarItem) -> bool {
            let inner = self.ivars().inner.borrow();
            match item.kind() {
                ITEM_KIND_GROUP => inner
                    .group_nodes
                    .get(item.key().trim_start_matches("g:"))
                    .map(|v| !v.is_empty())
                    .unwrap_or(false),
                _ => inner
                    .nodes_by_id
                    .get(item.key())
                    .map(|n| !n.children.is_empty())
                    .unwrap_or(false),
            }
        }
    }
);

fn node_kind(n: &SidebarNode) -> u32 {
    if n.cell_type == "terminal" {
        ITEM_KIND_TERMINAL
    } else {
        ITEM_KIND_AGENT
    }
}

// ---------------------------------------------------------------------------
// Delegate
// ---------------------------------------------------------------------------

pub(crate) struct SidebarDelegateIvars {
    inner: Shared,
}

declare_class!(
    pub(crate) struct SidebarDelegate;

    unsafe impl ClassType for SidebarDelegate {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomSidebarDelegate";
    }

    impl DeclaredClass for SidebarDelegate {
        type Ivars = SidebarDelegateIvars;
    }

    unsafe impl NSObjectProtocol for SidebarDelegate {}
    unsafe impl NSControlTextEditingDelegate for SidebarDelegate {}
    unsafe impl NSOutlineViewDelegate for SidebarDelegate {}

    unsafe impl SidebarDelegate {
        #[method_id(outlineView:viewForTableColumn:item:)]
        fn view_for_item(
            &self,
            _outline: &NSOutlineView,
            _col: Option<&NSTableColumn>,
            item: &SidebarItem,
        ) -> Retained<NSView> {
            let mtm = MainThreadMarker::from(self);
            let inner = self.ivars().inner.borrow();
            build_cell_view(mtm, item, &inner)
        }

        #[method(outlineView:isGroupItem:)]
        fn is_group_item(&self, _outline: &NSOutlineView, item: &SidebarItem) -> bool {
            item.kind() == ITEM_KIND_GROUP
        }

        #[method(outlineView:shouldSelectItem:)]
        fn should_select(&self, _outline: &NSOutlineView, item: &SidebarItem) -> bool {
            item.kind() != ITEM_KIND_GROUP
        }

        #[method(outlineView:heightOfRowByItem:)]
        fn height_of_row(&self, _outline: &NSOutlineView, item: &SidebarItem) -> f64 {
            if item.kind() == ITEM_KIND_GROUP {
                22.0
            } else {
                42.0
            }
        }

        #[method(outlineViewSelectionDidChange:)]
        fn selection_did_change(&self, notification: &NSNotification) {
            // The notification's `object` is the outline view.
            let Some(obj) = (unsafe { notification.object() }) else { return };
            let Some(outline): Option<Retained<NSOutlineView>> =
                (unsafe { downcast_retained::<NSOutlineView>(obj) })
            else {
                return;
            };
            let row = unsafe { outline.selectedRow() };
            let id = if row < 0 {
                None
            } else {
                unsafe { outline.itemAtRow(row) }
                    .map(|i| unsafe { cast_retained::<SidebarItem>(i) })
                    .filter(|i| i.kind() != ITEM_KIND_GROUP)
                    .map(|i| i.key().to_string())
            };
            let bridge = self.ivars().inner.borrow().bridge.clone();
            let payload = match &id {
                Some(s) => serde_json::json!({ "id": s }),
                None => serde_json::json!({ "id": serde_json::Value::Null }),
            };
            if let Err(e) = bridge.dispatch("select_agent", payload) {
                tracing::warn!("select_agent dispatch failed: {e:?}");
            }
        }
    }
);

// ---------------------------------------------------------------------------
// Custom NSOutlineView subclass — overrides menuForEvent:
// ---------------------------------------------------------------------------

pub(crate) struct SidebarOutlineIvars {
    #[allow(dead_code)]
    menu_target: Retained<MenuActionTarget>,
}

declare_class!(
    pub(crate) struct SidebarOutlineView;

    unsafe impl ClassType for SidebarOutlineView {
        type Super = NSOutlineView;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomSidebarOutlineView";
    }

    impl DeclaredClass for SidebarOutlineView {
        type Ivars = SidebarOutlineIvars;
    }

    unsafe impl SidebarOutlineView {
        #[method_id(menuForEvent:)]
        fn menu_for_event(&self, event: &NSEvent) -> Option<Retained<NSMenu>> {
            // declare_class! wraps the method body's final expression into
            // IdReturnValue — early `return None` skips that wrapping, so we
            // channel everything through a single tail expression.
            let loc = unsafe { event.locationInWindow() };
            let local: NSPoint = unsafe {
                msg_send![self, convertPoint: loc, fromView: std::ptr::null::<NSView>()]
            };
            let row = unsafe { self.rowAtPoint(local) };
            if row < 0 {
                None
            } else {
                match unsafe { self.itemAtRow(row) } {
                    None => None,
                    Some(item) => {
                        let sidebar_item: Retained<SidebarItem> =
                            unsafe { cast_retained::<SidebarItem>(item) };
                        // Only force-select selectable rows (agents /
                        // terminals). Group rows stay visually neutral on
                        // right-click.
                        if sidebar_item.kind() != ITEM_KIND_GROUP {
                            let indexes = index_set_with_index(row as usize);
                            unsafe {
                                self.selectRowIndexes_byExtendingSelection(
                                    &indexes, false,
                                )
                            };
                        }
                        Some(build_context_menu(
                            &sidebar_item,
                            &self.ivars().menu_target,
                            MainThreadMarker::from(self),
                        ))
                    }
                }
            }
        }
    }
);

// ---------------------------------------------------------------------------
// Menu action target
// ---------------------------------------------------------------------------
//
// Every context-menu NSMenuItem has `target = menu_target, action =
// handleMenuAction:`, and `representedObject = NSString("<verb>|<arg>")`. The
// target parses + dispatches.

pub(crate) struct MenuActionIvars {
    inner: Shared,
}

declare_class!(
    pub(crate) struct MenuActionTarget;

    unsafe impl ClassType for MenuActionTarget {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomMenuActionTarget";
    }

    impl DeclaredClass for MenuActionTarget {
        type Ivars = MenuActionIvars;
    }

    unsafe impl MenuActionTarget {
        #[method(handleMenuAction:)]
        fn handle_menu_action(&self, sender: &NSMenuItem) {
            let Some(rep) = (unsafe { sender.representedObject() }) else {
                return;
            };
            let Some(ns) = (unsafe { downcast_retained::<NSString>(rep) }) else {
                return;
            };
            let encoded = ns.to_string();
            let mtm = MainThreadMarker::from(self);
            let bridge = self.ivars().inner.borrow().bridge.clone();
            handle_sidebar_action(&encoded, &bridge, mtm);
        }

        #[method(handleAddGroup:)]
        fn handle_add_group(&self, _sender: &NSButton) {
            let mtm = MainThreadMarker::from(self);
            let bridge = self.ivars().inner.borrow().bridge.clone();
            run_add_group(&bridge, mtm);
        }
    }
);

// ---------------------------------------------------------------------------
// Context menu builder
// ---------------------------------------------------------------------------

fn build_context_menu(
    item: &SidebarItem,
    target: &MenuActionTarget,
    mtm: MainThreadMarker,
) -> Retained<NSMenu> {
    let menu: Retained<NSMenu> = NSMenu::new(mtm);
    unsafe { menu.setAutoenablesItems(false) };

    let key = item.key().to_string();
    match item.kind() {
        ITEM_KIND_GROUP => {
            let name = key.trim_start_matches("g:");
            add_item(
                &menu,
                mtm,
                "Rename Group…",
                &format!("rename_group|{name}"),
                target,
            );
            add_item(
                &menu,
                mtm,
                "Add Agent…",
                &format!("add_agent|{name}"),
                target,
            );
            add_item(
                &menu,
                mtm,
                "Add Terminal…",
                &format!("add_terminal|{name}"),
                target,
            );
            menu.addItem(&NSMenuItem::separatorItem(mtm));
            add_item(
                &menu,
                mtm,
                "Remove Group",
                &format!("remove_group|{name}"),
                target,
            );
        }
        ITEM_KIND_AGENT => {
            add_item(&menu, mtm, "Rename…", &format!("rename_cell|{key}"), target);
            add_item(&menu, mtm, "Edit…", &format!("edit_agent|{key}"), target);
            add_item(&menu, mtm, "Relaunch", &format!("relaunch|{key}"), target);
            add_item(
                &menu,
                mtm,
                "Add Terminal…",
                &format!("add_child_terminal|{key}"),
                target,
            );
            menu.addItem(&NSMenuItem::separatorItem(mtm));
            add_item(
                &menu,
                mtm,
                "Remove Agent",
                &format!("remove_cell|{key}"),
                target,
            );
        }
        ITEM_KIND_TERMINAL => {
            add_item(&menu, mtm, "Rename…", &format!("rename_cell|{key}"), target);
            add_item(&menu, mtm, "Edit…", &format!("edit_terminal|{key}"), target);
            add_item(&menu, mtm, "Relaunch", &format!("relaunch|{key}"), target);
            menu.addItem(&NSMenuItem::separatorItem(mtm));
            add_item(
                &menu,
                mtm,
                "Remove Terminal",
                &format!("remove_cell|{key}"),
                target,
            );
        }
        _ => {}
    }
    menu
}

fn add_item(
    menu: &NSMenu,
    mtm: MainThreadMarker,
    title: &str,
    encoded_action: &str,
    target: &MenuActionTarget,
) {
    let item: Retained<NSMenuItem> = unsafe {
        let alloc = mtm.alloc::<NSMenuItem>();
        NSMenuItem::initWithTitle_action_keyEquivalent(
            alloc,
            &NSString::from_str(title),
            Some(sel!(handleMenuAction:)),
            &NSString::from_str(""),
        )
    };
    unsafe {
        item.setTarget(Some(target.as_ref()));
        item.setRepresentedObject(Some(&NSString::from_str(encoded_action)));
    }
    menu.addItem(&item);
}

// ---------------------------------------------------------------------------
// Sidebar-action dispatch
// ---------------------------------------------------------------------------

fn handle_sidebar_action(encoded: &str, bridge: &EngineBridge, mtm: MainThreadMarker) {
    let (verb, arg) = match encoded.split_once('|') {
        Some(t) => t,
        None => (encoded, ""),
    };
    match verb {
        "rename_group" => run_rename_group(bridge, mtm, arg),
        "remove_group" => run_remove_group(bridge, mtm, arg),
        "add_agent" => run_add_agent(bridge, mtm, arg),
        "add_terminal" => run_add_terminal(bridge, mtm, arg, None),
        "add_child_terminal" => {
            let group = resolve_group_for_agent(bridge, arg).unwrap_or_default();
            run_add_terminal(bridge, mtm, &group, Some(arg));
        }
        "rename_cell" => run_rename_cell(bridge, mtm, arg),
        "edit_agent" => run_edit_agent(bridge, mtm, arg),
        "edit_terminal" => run_edit_agent(bridge, mtm, arg),
        "relaunch" => run_relaunch(bridge, arg),
        "remove_cell" => run_remove_cell(bridge, mtm, arg),
        other => tracing::warn!("unknown sidebar action: {other}"),
    }
}

fn resolve_group_for_agent(bridge: &EngineBridge, id: &str) -> Option<String> {
    let snap = bridge.snapshot();
    snap.find_agent(id).map(|a| a.group.clone())
}

fn run_add_group(bridge: &EngineBridge, mtm: MainThreadMarker) {
    let Some(vals) = prompt_form(
        mtm,
        "New Group",
        Some("Groups organize agents and terminals."),
        vec![FormField::new("Name", "name").with_placeholder("Engineering")],
        "Create",
    ) else {
        return;
    };
    let name = vals.get("name").cloned().unwrap_or_default();
    if name.trim().is_empty() {
        return;
    }
    if let Err(e) = bridge.dispatch("add_group", serde_json::json!({ "name": name })) {
        tracing::warn!("add_group failed: {e:?}");
    }
}

fn run_rename_group(bridge: &EngineBridge, mtm: MainThreadMarker, from: &str) {
    let Some(vals) = prompt_form(
        mtm,
        "Rename Group",
        None,
        vec![FormField::new("Name", "name").with_initial(from)],
        "Rename",
    ) else {
        return;
    };
    let to = vals.get("name").cloned().unwrap_or_default();
    if to.trim().is_empty() || to == from {
        return;
    }
    if let Err(e) = bridge.dispatch(
        "rename_group",
        serde_json::json!({ "from": from, "to": to }),
    ) {
        tracing::warn!("rename_group failed: {e:?}");
    }
}

fn run_remove_group(bridge: &EngineBridge, mtm: MainThreadMarker, name: &str) {
    if !confirm(
        mtm,
        &format!("Remove group “{name}”?"),
        Some("All agents and terminals in this group will be removed. This cannot be undone."),
        "Remove",
    ) {
        return;
    }
    if let Err(e) = bridge.dispatch("remove_group", serde_json::json!({ "name": name })) {
        tracing::warn!("remove_group failed: {e:?}");
    }
}

fn run_add_agent(bridge: &EngineBridge, mtm: MainThreadMarker, group: &str) {
    let Some(vals) = prompt_form(
        mtm,
        "New Agent",
        Some(&format!("In group “{group}”.")),
        vec![
            FormField::new("Name", "name").with_placeholder("worker"),
            FormField::new("Command", "command").with_placeholder("claude"),
            FormField::new("Directory", "directory").with_placeholder("leave blank for current"),
        ],
        "Create",
    ) else {
        return;
    };
    let name = vals.get("name").cloned().unwrap_or_default();
    if name.trim().is_empty() {
        return;
    }
    let mut body = serde_json::json!({ "name": name, "group": group });
    if let Some(c) = vals.get("command").filter(|s| !s.trim().is_empty()) {
        body["command"] = serde_json::Value::String(c.clone());
    }
    if let Some(d) = vals.get("directory").filter(|s| !s.trim().is_empty()) {
        body["directory"] = serde_json::Value::String(d.clone());
    }
    if let Err(e) = bridge.dispatch("add_agent", body) {
        tracing::warn!("add_agent failed: {e:?}");
    }
}

fn run_add_terminal(
    bridge: &EngineBridge,
    mtm: MainThreadMarker,
    group: &str,
    parent_id: Option<&str>,
) {
    let msg = match parent_id {
        Some(_) => "As a child terminal under the selected agent.".to_string(),
        None => format!("In group “{group}”."),
    };
    let Some(vals) = prompt_form(
        mtm,
        "New Terminal",
        Some(&msg),
        vec![
            FormField::new("Name", "name").with_placeholder("logs"),
            FormField::new("Command", "command").with_placeholder("/bin/zsh"),
            FormField::new("Directory", "directory").with_placeholder("leave blank for current"),
        ],
        "Create",
    ) else {
        return;
    };
    let name = vals.get("name").cloned().unwrap_or_default();
    if name.trim().is_empty() || group.is_empty() {
        return;
    }
    let mut body = serde_json::json!({ "name": name, "group": group });
    if let Some(pid) = parent_id {
        body["parent_id"] = serde_json::Value::String(pid.to_string());
    }
    if let Some(c) = vals.get("command").filter(|s| !s.trim().is_empty()) {
        body["command"] = serde_json::Value::String(c.clone());
    }
    if let Some(d) = vals.get("directory").filter(|s| !s.trim().is_empty()) {
        body["directory"] = serde_json::Value::String(d.clone());
    }
    if let Err(e) = bridge.dispatch("add_terminal", body) {
        tracing::warn!("add_terminal failed: {e:?}");
    }
}

fn run_rename_cell(bridge: &EngineBridge, mtm: MainThreadMarker, id: &str) {
    let snap = bridge.snapshot();
    let Some(cell) = snap.find_agent(id) else {
        return;
    };
    let Some(vals) = prompt_form(
        mtm,
        "Rename",
        None,
        vec![FormField::new("Name", "name").with_initial(cell.name.clone())],
        "Rename",
    ) else {
        return;
    };
    let new_name = vals.get("name").cloned().unwrap_or_default();
    if new_name.trim().is_empty() || new_name == cell.name {
        return;
    }
    if let Err(e) = bridge.dispatch(
        "update_agent",
        serde_json::json!({ "id": id, "fields": { "name": new_name } }),
    ) {
        tracing::warn!("update_agent (rename) failed: {e:?}");
    }
}

fn run_edit_agent(bridge: &EngineBridge, mtm: MainThreadMarker, id: &str) {
    let snap = bridge.snapshot();
    let Some(cell) = snap.find_agent(id).cloned() else {
        return;
    };
    let Some(vals) = prompt_form(
        mtm,
        "Edit",
        Some(&format!("Group: {}", cell.group)),
        vec![
            FormField::new("Name", "name").with_initial(cell.name.clone()),
            FormField::new("Command", "command").with_initial(cell.command.clone()),
            FormField::new("Directory", "directory").with_initial(cell.directory.clone()),
        ],
        "Save",
    ) else {
        return;
    };
    let name = vals.get("name").cloned().unwrap_or_default();
    if name.trim().is_empty() {
        return;
    }
    let fields = serde_json::json!({
        "name": name,
        "command": vals.get("command").cloned().unwrap_or_default(),
        "directory": vals.get("directory").cloned().unwrap_or_default(),
    });
    if let Err(e) = bridge.dispatch(
        "update_agent",
        serde_json::json!({ "id": id, "fields": fields }),
    ) {
        tracing::warn!("update_agent (edit) failed: {e:?}");
    }
}

fn run_relaunch(bridge: &EngineBridge, id: &str) {
    if let Err(e) = bridge.dispatch("relaunch_agent", serde_json::json!({ "id": id })) {
        tracing::warn!("relaunch_agent failed: {e:?}");
    }
}

fn run_remove_cell(bridge: &EngineBridge, mtm: MainThreadMarker, id: &str) {
    let snap = bridge.snapshot();
    let name = snap
        .find_agent(id)
        .map(|a| a.name.clone())
        .unwrap_or_else(|| "this cell".to_string());
    if !confirm(
        mtm,
        &format!("Remove “{name}”?"),
        Some("Its child terminals (if any) will also be removed."),
        "Remove",
    ) {
        return;
    }
    if let Err(e) = bridge.dispatch("remove_agent", serde_json::json!({ "id": id })) {
        tracing::warn!("remove_agent failed: {e:?}");
    }
}

// ---------------------------------------------------------------------------
// Cell view rendering
// ---------------------------------------------------------------------------

fn build_cell_view(
    mtm: MainThreadMarker,
    item: &SidebarItem,
    inner: &SidebarInner,
) -> Retained<NSView> {
    let width = 240.0;
    let (height, content) = match item.kind() {
        ITEM_KIND_GROUP => {
            let name = item.key().trim_start_matches("g:").to_string();
            (22.0, CellContent::Group { name })
        }
        _ => {
            let node = inner.nodes_by_id.get(item.key()).cloned();
            let h = 42.0;
            (h, CellContent::Node { node })
        }
    };

    let container: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        NSView::initWithFrame(
            alloc,
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(width, height)),
        )
    };
    unsafe {
        container.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewWidthSizable);
    }

    match content {
        CellContent::Group { name } => {
            let label = make_label(
                mtm,
                NSRect::new(NSPoint::new(0.0, 3.0), NSSize::new(width, 16.0)),
                &name.to_uppercase(),
                11.0,
                true,
                true, // secondary color
            );
            unsafe { container.addSubview(&label) };
        }
        CellContent::Node { node } => {
            let (title, subtitle) = match node {
                Some(n) => {
                    let dot = status_dot(&n.status);
                    let mut title = format!("{dot}  {}", n.name);
                    if n.needs_attention {
                        title.push_str("  !");
                    }
                    let mut parts: Vec<String> = Vec::new();
                    if let Some(t) = &n.type_label {
                        parts.push(t.clone());
                    }
                    if let Some(s) = &n.subtitle {
                        parts.push(s.clone());
                    }
                    (title, parts.join(" · "))
                }
                None => (format!("● {}", item.key()), String::new()),
            };

            let title_label = make_label(
                mtm,
                NSRect::new(NSPoint::new(0.0, 20.0), NSSize::new(width, 18.0)),
                &title,
                13.0,
                false,
                false,
            );
            unsafe { container.addSubview(&title_label) };
            if !subtitle.is_empty() {
                let sub_label = make_label(
                    mtm,
                    NSRect::new(NSPoint::new(0.0, 3.0), NSSize::new(width, 14.0)),
                    &subtitle,
                    11.0,
                    false,
                    true,
                );
                unsafe { container.addSubview(&sub_label) };
            }
        }
    }
    container
}

enum CellContent {
    Group { name: String },
    Node { node: Option<SidebarNode> },
}

fn make_label(
    mtm: MainThreadMarker,
    frame: NSRect,
    text: &str,
    font_size: f64,
    bold: bool,
    secondary: bool,
) -> Retained<NSTextField> {
    let tf: Retained<NSTextField> = unsafe {
        let alloc = mtm.alloc::<NSTextField>();
        NSTextField::initWithFrame(alloc, frame)
    };
    unsafe {
        tf.setStringValue(&NSString::from_str(text));
        tf.setEditable(false);
        tf.setSelectable(false);
        tf.setBezeled(false);
        tf.setBordered(false);
        tf.setDrawsBackground(false);
        let font = if bold {
            NSFont::boldSystemFontOfSize(font_size)
        } else {
            NSFont::systemFontOfSize(font_size)
        };
        tf.setFont(Some(&font));
        let color = if secondary {
            NSColor::secondaryLabelColor()
        } else {
            NSColor::labelColor()
        };
        tf.setTextColor(Some(&color));
    }
    tf
}

fn status_dot(status: &str) -> &'static str {
    match status {
        "running" => "●",
        "error" => "✗",
        _ => "○",
    }
}

// ---------------------------------------------------------------------------
// SidebarView — public handle
// ---------------------------------------------------------------------------

/// Handle for the mounted sidebar — hold this for the lifetime of the
/// window; dropping it releases the Cocoa object graph (outline view, data
/// source, delegate, menu target) which is otherwise leaked.
///
/// `data_source`, `delegate`, `menu_target` are stored only to keep them
/// alive while Cocoa references them weakly (`NSOutlineView.dataSource` is a
/// weak reference; `NSMenuItem.target` doesn't retain).
#[derive(Clone)]
pub struct SidebarView {
    pub container: Retained<NSView>,
    outline: Retained<SidebarOutlineView>,
    #[allow(dead_code)]
    data_source: Retained<SidebarDataSource>,
    #[allow(dead_code)]
    delegate: Retained<SidebarDelegate>,
    #[allow(dead_code)]
    menu_target: Retained<MenuActionTarget>,
    inner: Shared,
}

impl SidebarView {
    pub fn install(mtm: MainThreadMarker, bridge: EngineBridge) -> Self {
        let inner: Shared = Rc::new(RefCell::new(SidebarInner::new(bridge)));

        let menu_target: Retained<MenuActionTarget> = {
            let ivars = MenuActionIvars {
                inner: inner.clone(),
            };
            let this = mtm.alloc::<MenuActionTarget>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };

        let data_source: Retained<SidebarDataSource> = {
            let ivars = SidebarDataSourceIvars {
                inner: inner.clone(),
            };
            let this = mtm.alloc::<SidebarDataSource>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };

        let delegate: Retained<SidebarDelegate> = {
            let ivars = SidebarDelegateIvars {
                inner: inner.clone(),
            };
            let this = mtm.alloc::<SidebarDelegate>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };

        // Container: vertical stack of [button bar] + [scrolled outline].
        let container: Retained<NSView> = unsafe {
            let alloc = mtm.alloc::<NSView>();
            let v = NSView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(280.0, 760.0)),
            );
            v.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewHeightSizable,
            );
            v
        };

        // "+ Add Group" button at the top.
        let button_bar_height: f64 = 36.0;
        let button: Retained<NSButton> = unsafe {
            let alloc = mtm.alloc::<NSButton>();
            let b = NSButton::initWithFrame(
                alloc,
                NSRect::new(
                    NSPoint::new(8.0, 760.0 - button_bar_height + 4.0),
                    NSSize::new(120.0, 24.0),
                ),
            );
            b.setTitle(&NSString::from_str("+ Add Group"));
            b.setTarget(Some(menu_target.as_ref()));
            b.setAction(Some(sel!(handleAddGroup:)));
            b.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewMinYMargin);
            b
        };
        unsafe { container.addSubview(&button) };

        // NSScrollView + NSOutlineView below.
        let scroll_frame = NSRect::new(
            NSPoint::new(0.0, 0.0),
            NSSize::new(280.0, 760.0 - button_bar_height),
        );
        let scroll: Retained<NSScrollView> = unsafe {
            let alloc = mtm.alloc::<NSScrollView>();
            let s = NSScrollView::initWithFrame(alloc, scroll_frame);
            s.setHasVerticalScroller(true);
            s.setAutohidesScrollers(true);
            s.setBorderType(NSBorderType::NSNoBorder);
            s.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewHeightSizable,
            );
            s
        };

        let outline: Retained<SidebarOutlineView> = {
            let ivars = SidebarOutlineIvars {
                menu_target: menu_target.clone(),
            };
            let this = mtm.alloc::<SidebarOutlineView>().set_ivars(ivars);
            let this: Retained<SidebarOutlineView> = unsafe { msg_send_id![super(this), init] };
            unsafe { this.setFrame(scroll_frame) };
            this
        };
        // Single column — view-based outline. The identifier isn't observed
        // anywhere in our code; default is fine.
        let col: Retained<NSTableColumn> = unsafe {
            let alloc = mtm.alloc::<NSTableColumn>();
            msg_send_id![alloc, init]
        };
        unsafe {
            col.setWidth(240.0);
            outline.addTableColumn(&col);
            outline.setOutlineTableColumn(Some(&col));
            // Hide the default column header bar (NSTableColumn's default
            // title is "Field", which leaks through otherwise).
            outline.setHeaderView(None);
            outline.setIndentationPerLevel(14.0);
            // Source-list selection style. objc2-app-kit 0.2 doesn't expose
            // the variant name we'd expect, so fall through to the raw
            // NSInteger via msg_send.
            let _: () = msg_send![
                &outline,
                setSelectionHighlightStyle: NS_SELECTION_HIGHLIGHT_SOURCE_LIST
            ];
            outline.setFloatsGroupRows(true);
            outline.setRowHeight(42.0);

            // Protocol-typed setters: NSOutlineView wants ProtocolObject<dyn
            // NSOutlineViewDataSource> / NSOutlineViewDelegate.
            let ds_proto = ProtocolObject::from_ref::<SidebarDataSource>(&data_source);
            outline.setDataSource(Some(ds_proto));
            let del_proto = ProtocolObject::from_ref::<SidebarDelegate>(&delegate);
            outline.setDelegate(Some(del_proto));
            scroll.setDocumentView(Some(&outline));
        }
        unsafe { container.addSubview(&scroll) };

        SidebarView {
            container,
            outline,
            data_source,
            delegate,
            menu_target,
            inner,
        }
    }

    /// Rebuild the outline from a fresh snapshot. No-op when the tree JSON
    /// signature hasn't changed since the last reload.
    pub fn reload_if_changed(&self, snapshot: &crate::bridge::MatrixStateSnapshot) {
        let new_tree = render::build_sidebar_tree(snapshot);
        let new_sig = serde_json::to_string(&new_tree).unwrap_or_default();
        {
            let inner = self.inner.borrow();
            if inner.signature == new_sig && !inner.signature.is_empty() {
                return;
            }
        }
        {
            let mut inner = self.inner.borrow_mut();
            inner.tree = new_tree;
            inner.signature = new_sig;
            inner.reindex();
        }
        unsafe { self.outline.reloadData() };
        // Auto-expand all groups + agents so terminals are visible by default.
        self.expand_all();
        // Restore selection from the engine's selected_agent_id.
        self.restore_selection();
    }

    fn expand_all(&self) {
        let inner = self.inner.borrow();
        let groups: Vec<String> = inner.tree.groups.iter().map(|g| g.name.clone()).collect();
        drop(inner);
        for name in groups {
            let item = {
                let mut inner = self.inner.borrow_mut();
                inner.item_for(group_key(&name), ITEM_KIND_GROUP)
            };
            unsafe { self.outline.expandItem(Some(item.as_ref())) };
        }
        // Expand each agent with children.
        let agents_with_children: Vec<String> = {
            let inner = self.inner.borrow();
            inner
                .nodes_by_id
                .values()
                .filter(|n| !n.children.is_empty())
                .map(|n| n.id.clone())
                .collect()
        };
        for id in agents_with_children {
            let item = {
                let mut inner = self.inner.borrow_mut();
                inner.item_for(id, ITEM_KIND_AGENT)
            };
            unsafe { self.outline.expandItem(Some(item.as_ref())) };
        }
    }

    fn restore_selection(&self) {
        let selected_id = self.inner.borrow().tree.selected_id.clone();
        let Some(id) = selected_id else {
            unsafe {
                self.outline
                    .selectRowIndexes_byExtendingSelection(&NSIndexSet::new(), false);
            }
            return;
        };
        // Find the kind by looking up in nodes_by_id.
        let kind = {
            let inner = self.inner.borrow();
            inner
                .nodes_by_id
                .get(&id)
                .map(node_kind)
                .unwrap_or(ITEM_KIND_AGENT)
        };
        let item = {
            let mut inner = self.inner.borrow_mut();
            inner.item_for(id, kind)
        };
        let row = unsafe { self.outline.rowForItem(Some(item.as_ref())) };
        if row < 0 {
            return;
        }
        let indexes = index_set_with_index(row as usize);
        unsafe {
            self.outline
                .selectRowIndexes_byExtendingSelection(&indexes, false);
            self.outline.scrollRowToVisible(row);
        }
    }
}
