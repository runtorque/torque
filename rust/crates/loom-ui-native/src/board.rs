//! Native Board panel — all-lanes-visible kanban. Each non-Archived lane
//! renders as a column with its own header, inline "+ Add task" field,
//! and scrollable NSTableView of cards. Columns sit side-by-side in a
//! horizontal NSStackView inside a horizontal NSScrollView.
//!
//! Data flow:
//!
//! 1. [`BoardView::install`] builds the outer scroller + stack and seeds
//!    an empty column list.
//! 2. [`BoardView::reload_if_changed`] is called on every refresh tick.
//!    It computes a new [`render::BoardColumns`] from the snapshot and a
//!    JSON signature. When the lane *set* has changed, the column widgets
//!    are rebuilt; otherwise each column's table just `reloadData`.
//! 3. Drag-drop: each column's NSTableView is both a drag source (for
//!    within-lane reorder) and a drop target. On drop, the data source
//!    compares the source task's current lane against its own lane and
//!    dispatches `board_reorder_task` (same lane) or `board_move_task`
//!    (cross-lane).
//! 4. Inline add-task: each column's NSTextField has its own lane in
//!    ivars; Enter dispatches `board_add_task` with that lane.
//! 5. Context menu on a card: Dispatch / Edit / Move to lane / Remove.

use std::cell::RefCell;
use std::rc::Rc;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObjectProtocol, ProtocolObject};
use objc2::{declare_class, msg_send, msg_send_id, mutability, sel, ClassType, DeclaredClass};
use objc2_app_kit::{
    NSAutoresizingMaskOptions, NSBorderType, NSColor, NSControlTextEditingDelegate, NSDraggingInfo,
    NSEvent, NSFont, NSMenu, NSMenuItem, NSPasteboard, NSPasteboardItem, NSPasteboardWriting,
    NSScrollView, NSTableColumn, NSTableView, NSTableViewDataSource, NSTableViewDelegate,
    NSTextField, NSView,
};
use objc2_foundation::{MainThreadMarker, NSObject, NSPoint, NSRect, NSSize, NSString};

use crate::bridge::{EngineBridge, MatrixStateSnapshot};
use crate::modal::{confirm, prompt_form, FormField};
use crate::render::{self, BoardRow};

const ROW_HEIGHT: f64 = 48.0;
const INDENT_PER_DEPTH: f64 = 20.0;
const ADD_ROW_HEIGHT: f64 = 28.0;

/// Custom pasteboard type carrying a serialized task id during drags from
/// the Board. Scoped to this app — no other process should consume it.
const TASK_DRAG_TYPE: &str = "com.anthropic.loom.task";

// Cocoa's NSDragOperation is a NSUInteger bitmask. Move = 16.
const NS_DRAG_OPERATION_MOVE: usize = 16;

// ---------------------------------------------------------------------------
// Shared inner state
// ---------------------------------------------------------------------------

struct BoardInner {
    group_filter: Option<String>,
    columns: Vec<render::BoardColumn>,
    signature: String,
    bridge: EngineBridge,
}

impl BoardInner {
    fn new(bridge: EngineBridge) -> Self {
        Self {
            group_filter: None,
            columns: Vec::new(),
            signature: String::new(),
            bridge,
        }
    }

    fn rows_for_lane(&self, lane: &str) -> &[BoardRow] {
        self.columns
            .iter()
            .find(|c| c.lane == lane)
            .map(|c| c.rows.as_slice())
            .unwrap_or(&[])
    }

    fn lane_of_task(&self, task_id: &str) -> Option<String> {
        for col in &self.columns {
            if col.rows.iter().any(|r| r.id == task_id) {
                return Some(col.lane.clone());
            }
        }
        None
    }
}

type Shared = Rc<RefCell<BoardInner>>;

// ---------------------------------------------------------------------------
// Data source
// ---------------------------------------------------------------------------

pub(crate) struct BoardDataSourceIvars {
    inner: Shared,
    /// Which lane's rows this data source serves. Each column has its own
    /// data source + table, so the lane can be read straight from ivars.
    lane: String,
}

declare_class!(
    pub(crate) struct BoardDataSource;

    unsafe impl ClassType for BoardDataSource {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomBoardDataSource";
    }

    impl DeclaredClass for BoardDataSource {
        type Ivars = BoardDataSourceIvars;
    }

    unsafe impl NSObjectProtocol for BoardDataSource {}
    unsafe impl NSTableViewDataSource for BoardDataSource {}

    unsafe impl BoardDataSource {
        #[method(numberOfRowsInTableView:)]
        fn number_of_rows(&self, _table: &NSTableView) -> isize {
            let ivars = self.ivars();
            ivars.inner.borrow().rows_for_lane(&ivars.lane).len() as isize
        }

        // --- drag source -------------------------------------------------

        #[method_id(tableView:pasteboardWriterForRow:)]
        fn pasteboard_writer_for_row(
            &self,
            _table: &NSTableView,
            row: isize,
        ) -> Option<Retained<ProtocolObject<dyn NSPasteboardWriting>>> {
            let ivars = self.ivars();
            let id = {
                let inner = ivars.inner.borrow();
                inner
                    .rows_for_lane(&ivars.lane)
                    .get(row as usize)
                    .map(|r| r.id.clone())
            };
            match id {
                None => None,
                Some(id) => {
                    let mtm = MainThreadMarker::from(self);
                    let item: Retained<NSPasteboardItem> = unsafe {
                        let alloc = mtm.alloc::<NSPasteboardItem>();
                        msg_send_id![alloc, init]
                    };
                    let pbtype = NSString::from_str(TASK_DRAG_TYPE);
                    unsafe {
                        let _: bool = msg_send![
                            &item,
                            setString: &*NSString::from_str(&id),
                            forType: &*pbtype
                        ];
                    }
                    Some(ProtocolObject::from_retained(item))
                }
            }
        }

        // --- drag destination (within-lane reorder) ---------------------

        #[method(tableView:validateDrop:proposedRow:proposedDropOperation:)]
        fn validate_drop(
            &self,
            _table: &NSTableView,
            _info: &ProtocolObject<dyn NSDraggingInfo>,
            _row: isize,
            _op: isize,
        ) -> usize {
            // Only accept drops between rows (NSTableViewDropAbove = 1).
            // `op` 0 = On (drop onto a row), 1 = Above (between rows). We
            // accept both and treat them the same for reorder.
            NS_DRAG_OPERATION_MOVE
        }

        #[method(tableView:acceptDrop:row:dropOperation:)]
        fn accept_drop(
            &self,
            _table: &NSTableView,
            info: &ProtocolObject<dyn NSDraggingInfo>,
            row: isize,
            _op: isize,
        ) -> bool {
            let pb: Retained<NSPasteboard> = unsafe { info.draggingPasteboard() };
            let pbtype = NSString::from_str(TASK_DRAG_TYPE);
            let id_opt: Option<Retained<NSString>> =
                unsafe { msg_send_id![&pb, stringForType: &*pbtype] };
            match id_opt {
                None => false,
                Some(id_ns) => {
                    let ivars = self.ivars();
                    let task_id = id_ns.to_string();
                    let target_position = row.max(0) as i64;
                    let (bridge, source_lane) = {
                        let inner = ivars.inner.borrow();
                        (inner.bridge.clone(), inner.lane_of_task(&task_id))
                    };
                    // Foreign-lane drop → move. Same-lane drop → reorder.
                    let dispatched = if source_lane
                        .as_deref()
                        .map(|l| l != ivars.lane)
                        .unwrap_or(false)
                    {
                        bridge.dispatch(
                            "board_move_task",
                            serde_json::json!({
                                "id": task_id,
                                "lane": ivars.lane,
                            }),
                        )
                    } else {
                        bridge.dispatch(
                            "board_reorder_task",
                            serde_json::json!({
                                "id": task_id,
                                "position": target_position,
                            }),
                        )
                    };
                    match dispatched {
                        Ok(_) => {
                            ivars.inner.borrow_mut().signature = String::new();
                            true
                        }
                        Err(e) => {
                            tracing::warn!(
                                "board drop dispatch failed ({}): {e:?}",
                                ivars.lane
                            );
                            false
                        }
                    }
                }
            }
        }
    }
);

// ---------------------------------------------------------------------------
// Delegate (view-based rendering + selection)
// ---------------------------------------------------------------------------

pub(crate) struct BoardDelegateIvars {
    inner: Shared,
    lane: String,
}

declare_class!(
    pub(crate) struct BoardDelegate;

    unsafe impl ClassType for BoardDelegate {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomBoardDelegate";
    }

    impl DeclaredClass for BoardDelegate {
        type Ivars = BoardDelegateIvars;
    }

    unsafe impl NSObjectProtocol for BoardDelegate {}
    unsafe impl NSControlTextEditingDelegate for BoardDelegate {}
    unsafe impl NSTableViewDelegate for BoardDelegate {}

    unsafe impl BoardDelegate {
        #[method_id(tableView:viewForTableColumn:row:)]
        fn view_for_row(
            &self,
            _table: &NSTableView,
            _col: Option<&NSTableColumn>,
            row: isize,
        ) -> Retained<NSView> {
            let mtm = MainThreadMarker::from(self);
            let ivars = self.ivars();
            let inner = ivars.inner.borrow();
            let idx = row as usize;
            let row_data = inner
                .rows_for_lane(&ivars.lane)
                .get(idx)
                .cloned()
                .unwrap_or_else(placeholder_row);
            build_row_view(mtm, &row_data)
        }

        #[method(tableView:heightOfRow:)]
        fn height_of_row(&self, _table: &NSTableView, _row: isize) -> f64 {
            ROW_HEIGHT
        }
    }
);

fn placeholder_row() -> BoardRow {
    BoardRow {
        id: String::new(),
        title: "—".into(),
        lane: String::new(),
        group: String::new(),
        labels: Vec::new(),
        action_name: String::new(),
        agent_id: String::new(),
        assignee: String::new(),
        status: String::new(),
        depth: 0,
        has_children: false,
        is_done: false,
        in_active_lane: true,
    }
}

// ---------------------------------------------------------------------------
// Custom NSTableView subclass — overrides menuForEvent: for card context menu
// ---------------------------------------------------------------------------

pub(crate) struct BoardTableIvars {
    inner: Shared,
    menu_target: Retained<BoardMenuTarget>,
    lane: String,
}

declare_class!(
    pub(crate) struct BoardTableView;

    unsafe impl ClassType for BoardTableView {
        type Super = NSTableView;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomBoardTableView";
    }

    impl DeclaredClass for BoardTableView {
        type Ivars = BoardTableIvars;
    }

    unsafe impl BoardTableView {
        #[method_id(menuForEvent:)]
        fn menu_for_event(&self, event: &NSEvent) -> Option<Retained<NSMenu>> {
            let loc = unsafe { event.locationInWindow() };
            let local: NSPoint = unsafe {
                msg_send![self, convertPoint: loc, fromView: std::ptr::null::<NSView>()]
            };
            let row = unsafe { self.rowAtPoint(local) };
            if row < 0 {
                None
            } else {
                let ivars = self.ivars();
                let (card_opt, lanes) = {
                    let inner = ivars.inner.borrow();
                    (
                        inner
                            .rows_for_lane(&ivars.lane)
                            .get(row as usize)
                            .cloned(),
                        inner.columns.iter().map(|c| c.lane.clone()).collect::<Vec<_>>(),
                    )
                };
                match card_opt {
                    None => None,
                    Some(card) => {
                        let mtm = MainThreadMarker::from(self);
                        let index_set: Retained<objc2_foundation::NSIndexSet> = unsafe {
                            msg_send_id![
                                objc2_foundation::NSIndexSet::class(),
                                indexSetWithIndex: row as usize
                            ]
                        };
                        unsafe {
                            self.selectRowIndexes_byExtendingSelection(&index_set, false)
                        };
                        Some(build_card_menu(
                            mtm,
                            &card,
                            &lanes,
                            &ivars.menu_target,
                        ))
                    }
                }
            }
        }
    }
);

// ---------------------------------------------------------------------------
// Menu target — handles context-menu actions, tab segment changes, and
// inline add-task submissions.
// ---------------------------------------------------------------------------

pub(crate) struct BoardMenuTargetIvars {
    inner: Shared,
    /// `NSControl.tag` on each column's "+ Add task" field is set to the
    /// column index. This vector maps tag → (lane, field) so the submit
    /// handler can read the text + dispatch with the right lane.
    add_fields: RefCell<Vec<(String, Retained<NSTextField>)>>,
}

declare_class!(
    pub(crate) struct BoardMenuTarget;

    unsafe impl ClassType for BoardMenuTarget {
        type Super = NSObject;
        type Mutability = mutability::MainThreadOnly;
        const NAME: &'static str = "LoomBoardMenuTarget";
    }

    impl DeclaredClass for BoardMenuTarget {
        type Ivars = BoardMenuTargetIvars;
    }

    unsafe impl BoardMenuTarget {
        #[method(handleCardMenu:)]
        fn handle_card_menu(&self, sender: &NSMenuItem) {
            let Some(rep) = (unsafe { sender.representedObject() }) else { return };
            let Some(ns) = (unsafe { downcast_retained::<NSString>(rep) }) else { return };
            let encoded = ns.to_string();
            let mtm = MainThreadMarker::from(self);
            let (bridge, lanes) = {
                let inner = self.ivars().inner.borrow();
                (
                    inner.bridge.clone(),
                    inner.columns.iter().map(|c| c.lane.clone()).collect::<Vec<_>>(),
                )
            };
            handle_card_action(&encoded, &bridge, mtm, &lanes);
        }

        #[method(handleAddTaskSubmit:)]
        fn handle_add_task_submit(&self, sender: &NSTextField) {
            let tag = unsafe { sender.tag() };
            let fields = self.ivars().add_fields.borrow();
            let Some((lane, field)) = fields.get(tag as usize).cloned() else {
                return;
            };
            drop(fields);
            let text = unsafe { field.stringValue() }.to_string();
            let trimmed = text.trim();
            if trimmed.is_empty() {
                return;
            }
            let (bridge, group) = {
                let inner = self.ivars().inner.borrow();
                let group = inner
                    .group_filter
                    .clone()
                    .unwrap_or_else(|| "default".to_string());
                (inner.bridge.clone(), group)
            };
            let body = serde_json::json!({
                "task": trimmed,
                "group": group,
                "lane": lane,
            });
            if let Err(e) = bridge.dispatch("board_add_task", body) {
                tracing::warn!("board_add_task failed: {e:?}");
                return;
            }
            unsafe { field.setStringValue(&NSString::from_str("")) };
            self.ivars().inner.borrow_mut().signature = String::new();
        }
    }
);

// ---------------------------------------------------------------------------
// Context menu builder
// ---------------------------------------------------------------------------

fn build_card_menu(
    mtm: MainThreadMarker,
    card: &BoardRow,
    lanes: &[String],
    target: &BoardMenuTarget,
) -> Retained<NSMenu> {
    let menu: Retained<NSMenu> = NSMenu::new(mtm);
    unsafe { menu.setAutoenablesItems(false) };
    add_menu(
        &menu,
        mtm,
        "Dispatch",
        &format!("dispatch|{}", card.id),
        target,
    );
    add_menu(&menu, mtm, "Edit…", &format!("edit|{}", card.id), target);

    // Move to lane submenu.
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
    for lane in lanes {
        if lane == &card.lane {
            continue;
        }
        add_menu(
            &submenu,
            mtm,
            lane,
            &format!("move|{}|{}", card.id, lane),
            target,
        );
    }
    move_item.setSubmenu(Some(&submenu));
    menu.addItem(&move_item);

    menu.addItem(&NSMenuItem::separatorItem(mtm));
    add_menu(&menu, mtm, "Remove", &format!("remove|{}", card.id), target);
    menu
}

fn add_menu(
    menu: &NSMenu,
    mtm: MainThreadMarker,
    title: &str,
    encoded: &str,
    target: &BoardMenuTarget,
) {
    let item: Retained<NSMenuItem> = unsafe {
        let alloc = mtm.alloc::<NSMenuItem>();
        NSMenuItem::initWithTitle_action_keyEquivalent(
            alloc,
            &NSString::from_str(title),
            Some(sel!(handleCardMenu:)),
            &NSString::from_str(""),
        )
    };
    unsafe {
        item.setTarget(Some(target.as_ref()));
        item.setRepresentedObject(Some(&NSString::from_str(encoded)));
    }
    menu.addItem(&item);
}

fn handle_card_action(
    encoded: &str,
    bridge: &EngineBridge,
    mtm: MainThreadMarker,
    lanes: &[String],
) {
    let mut parts = encoded.splitn(3, '|');
    let verb = parts.next().unwrap_or("");
    let id = parts.next().unwrap_or("");
    let arg = parts.next().unwrap_or("");
    match verb {
        "dispatch" => {
            if let Err(e) = bridge.dispatch("dispatch_task", serde_json::json!({ "task_id": id })) {
                tracing::warn!("dispatch_task failed: {e:?}");
            }
        }
        "edit" => {
            let snap = bridge.snapshot();
            let Some(card) = snap.board_tasks.iter().find(|t| t.id == id).cloned() else {
                return;
            };
            let Some(vals) = prompt_form(
                mtm,
                "Edit task",
                Some(&format!("Group: {}", card.group)),
                vec![FormField::new("Title", "title").with_initial(card.task.clone())],
                "Save",
            ) else {
                return;
            };
            let title = vals.get("title").cloned().unwrap_or_default();
            if title.trim().is_empty() || title == card.task {
                return;
            }
            if let Err(e) = bridge.dispatch(
                "board_update_task",
                serde_json::json!({ "id": id, "fields": { "task": title } }),
            ) {
                tracing::warn!("board_update_task failed: {e:?}");
            }
        }
        "move" => {
            // Validate that lane exists before dispatching.
            if !lanes.iter().any(|l| l == arg) {
                return;
            }
            if let Err(e) = bridge.dispatch(
                "board_move_task",
                serde_json::json!({ "id": id, "lane": arg }),
            ) {
                tracing::warn!("board_move_task failed: {e:?}");
            }
        }
        "remove" => {
            if !confirm(
                mtm,
                "Remove task?",
                Some("This cannot be undone."),
                "Remove",
            ) {
                return;
            }
            if let Err(e) = bridge.dispatch("board_remove_task", serde_json::json!({ "id": id })) {
                tracing::warn!("board_remove_task failed: {e:?}");
            }
        }
        other => tracing::warn!("unknown card action: {other}"),
    }
}

// ---------------------------------------------------------------------------
// Row rendering
// ---------------------------------------------------------------------------

fn build_row_view(mtm: MainThreadMarker, row: &BoardRow) -> Retained<NSView> {
    let width = 600.0;
    let container: Retained<NSView> = unsafe {
        let alloc = mtm.alloc::<NSView>();
        NSView::initWithFrame(
            alloc,
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(width, ROW_HEIGHT)),
        )
    };
    unsafe {
        container.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewWidthSizable);
    }

    let indent = INDENT_PER_DEPTH * row.depth as f64;

    // Primary line: depth indent + prefix glyph (↳ for derived) + title.
    let prefix = if row.depth > 0 { "↳ " } else { "" };
    let done_mark = if row.is_done { "✓ " } else { "" };
    let title = format!("{prefix}{done_mark}{}", row.title);
    let title_label = make_label(
        mtm,
        NSRect::new(
            NSPoint::new(indent, 22.0),
            NSSize::new(width - indent, 20.0),
        ),
        &title,
        13.0,
        false,
        row.is_done || !row.in_active_lane,
    );
    unsafe { container.addSubview(&title_label) };

    // Secondary line: labels, action badge, assignee.
    let mut parts: Vec<String> = Vec::new();
    if !row.action_name.is_empty() {
        parts.push(format!("[{}]", row.action_name));
    }
    if !row.labels.is_empty() {
        parts.push(row.labels.join(" · "));
    }
    if !row.status.is_empty() {
        parts.push(format!("status: {}", row.status));
    }
    if !row.group.is_empty() {
        parts.push(row.group.clone());
    }
    let subtitle = parts.join("   ");
    if !subtitle.is_empty() {
        let sub_label = make_label(
            mtm,
            NSRect::new(NSPoint::new(indent, 4.0), NSSize::new(width - indent, 14.0)),
            &subtitle,
            11.0,
            false,
            true,
        );
        unsafe { container.addSubview(&sub_label) };
    }

    container
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

// ---------------------------------------------------------------------------
// objc2 helpers (local copies — sidebar.rs has the originals; can extract
// into a shared `ffi_util.rs` module later)
// ---------------------------------------------------------------------------

unsafe fn downcast_retained<T: ClassType>(obj: Retained<AnyObject>) -> Option<Retained<T>> {
    let cls = T::class();
    let is_kind: bool = msg_send![&*obj, isKindOfClass: cls];
    if is_kind {
        Some(Retained::cast::<T>(obj))
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// Public handle
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct BoardView {
    pub container: Retained<NSView>,
    /// Horizontal scroll container that holds the column stack. Document
    /// view is resized as columns are added/removed so overflow panes.
    #[allow(dead_code)]
    outer_scroll: Retained<NSScrollView>,
    /// The inline NSView whose subviews are the column containers, laid
    /// out left-to-right by `reload_if_changed`.
    column_host: Retained<NSView>,
    /// One per visible column. Rebuilt when the lane set changes;
    /// otherwise each holds its own table that just `reloadData`s.
    columns: Rc<RefCell<Vec<BoardColumnView>>>,
    #[allow(dead_code)]
    menu_target: Retained<BoardMenuTarget>,
    inner: Shared,
}

const COLUMN_WIDTH: f64 = 300.0;
const COLUMN_GAP: f64 = 8.0;
const COLUMN_HEADER_HEIGHT: f64 = 24.0;

/// Per-column widgets. Held by `BoardView.columns` so the Retained values
/// keep the Cocoa objects alive for NSTableView's weak references.
#[derive(Clone)]
struct BoardColumnView {
    lane: String,
    container: Retained<NSView>,
    #[allow(dead_code)]
    header: Retained<NSTextField>,
    #[allow(dead_code)]
    add_field: Retained<NSTextField>,
    table: Retained<BoardTableView>,
    #[allow(dead_code)]
    data_source: Retained<BoardDataSource>,
    #[allow(dead_code)]
    delegate: Retained<BoardDelegate>,
}

impl BoardView {
    pub fn install(mtm: MainThreadMarker, bridge: EngineBridge) -> Self {
        let inner: Shared = Rc::new(RefCell::new(BoardInner::new(bridge)));

        let menu_target: Retained<BoardMenuTarget> = {
            let ivars = BoardMenuTargetIvars {
                inner: inner.clone(),
                add_fields: RefCell::new(Vec::new()),
            };
            let this = mtm.alloc::<BoardMenuTarget>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };

        let container: Retained<NSView> = unsafe {
            let alloc = mtm.alloc::<NSView>();
            let v = NSView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(800.0, 320.0)),
            );
            v.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewHeightSizable,
            );
            v
        };

        // Outer horizontal scroll view — columns scroll horizontally when
        // the total width exceeds the visible area. Document view is a
        // plain NSView (`column_host`) we resize as columns change.
        let outer_scroll: Retained<NSScrollView> = unsafe {
            let alloc = mtm.alloc::<NSScrollView>();
            let s = NSScrollView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(800.0, 320.0)),
            );
            s.setHasHorizontalScroller(true);
            s.setHasVerticalScroller(false);
            s.setAutohidesScrollers(true);
            s.setBorderType(NSBorderType::NSNoBorder);
            s.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewHeightSizable,
            );
            s
        };

        let column_host: Retained<NSView> = unsafe {
            let alloc = mtm.alloc::<NSView>();
            let v = NSView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(800.0, 320.0)),
            );
            // Track the clip view's vertical size so the column strip always
            // fills the panel height. Width stays under our control (grows
            // with column count so the horizontal scroller kicks in).
            v.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewHeightSizable);
            v
        };
        unsafe {
            outer_scroll.setDocumentView(Some(&column_host));
            container.addSubview(&outer_scroll);
        }

        BoardView {
            container,
            outer_scroll,
            column_host,
            columns: Rc::new(RefCell::new(Vec::new())),
            menu_target,
            inner,
        }
    }

    pub fn reload_if_changed(&self, snapshot: &MatrixStateSnapshot) {
        // Always re-layout columns — cheap (a few setFrame calls) and makes
        // window resizes propagate even when the data is unchanged. Without
        // this, NSClipView anchors the shorter document view to its origin
        // (the bottom in non-flipped coords) and the whole strip floats to
        // the bottom of the panel.
        self.layout_columns();

        let group_filter = self.inner.borrow().group_filter.clone();
        let view = render::build_board_columns(snapshot, group_filter.as_deref());
        let new_sig = serde_json::to_string(&view).unwrap_or_default();
        {
            let inner = self.inner.borrow();
            if inner.signature == new_sig && !inner.signature.is_empty() {
                return;
            }
        }

        // Detect whether the lane set changed — only then do we rebuild
        // the NSView subtree. Individual row changes go through reloadData
        // on each table.
        let new_lanes: Vec<String> = view.columns.iter().map(|c| c.lane.clone()).collect();
        let lanes_changed = {
            let existing: Vec<String> = self
                .columns
                .borrow()
                .iter()
                .map(|c| c.lane.clone())
                .collect();
            existing != new_lanes
        };

        {
            let mut inner = self.inner.borrow_mut();
            inner.columns = view.columns.clone();
            inner.signature = new_sig;
        }

        if lanes_changed {
            let mtm =
                MainThreadMarker::new().expect("reload_if_changed must run on the main thread");
            self.rebuild_column_widgets(mtm, &new_lanes);
        }
        // Resize the inner column_host and reload each table's data.
        self.layout_columns();
        for col in self.columns.borrow().iter() {
            unsafe { col.table.reloadData() };
            unsafe {
                col.header
                    .setStringValue(&NSString::from_str(&self.header_text(&col.lane)))
            };
        }
    }

    fn header_text(&self, lane: &str) -> String {
        let inner = self.inner.borrow();
        let count = inner.rows_for_lane(lane).len();
        format!("{lane}  ·  {count}")
    }

    fn layout_columns(&self) {
        let columns = self.columns.borrow();
        let count = columns.len() as f64;
        let total_w = (count * COLUMN_WIDTH + (count.max(1.0) - 1.0) * COLUMN_GAP).max(1.0);
        let host_h = unsafe { self.outer_scroll.contentSize() }.height.max(1.0);
        unsafe {
            self.column_host.setFrame(NSRect::new(
                NSPoint::new(0.0, 0.0),
                NSSize::new(total_w, host_h),
            ));
        }
        for (i, col) in columns.iter().enumerate() {
            let x = i as f64 * (COLUMN_WIDTH + COLUMN_GAP);
            unsafe {
                col.container.setFrame(NSRect::new(
                    NSPoint::new(x, 0.0),
                    NSSize::new(COLUMN_WIDTH, host_h),
                ));
            }
        }
    }

    fn rebuild_column_widgets(&self, mtm: MainThreadMarker, lanes: &[String]) {
        // Tear down existing columns.
        {
            for col in self.columns.borrow().iter() {
                unsafe { col.container.removeFromSuperview() };
            }
        }
        self.columns.borrow_mut().clear();
        self.menu_target.ivars().add_fields.borrow_mut().clear();

        // Build one widget tree per lane.
        let mut new_cols: Vec<BoardColumnView> = Vec::with_capacity(lanes.len());
        for (i, lane) in lanes.iter().enumerate() {
            let col = self.build_column_widget(mtm, lane, i);
            unsafe { self.column_host.addSubview(&col.container) };
            self.menu_target
                .ivars()
                .add_fields
                .borrow_mut()
                .push((lane.clone(), col.add_field.clone()));
            new_cols.push(col);
        }
        *self.columns.borrow_mut() = new_cols;
    }

    fn build_column_widget(
        &self,
        mtm: MainThreadMarker,
        lane: &str,
        tag: usize,
    ) -> BoardColumnView {
        let container: Retained<NSView> = unsafe {
            let alloc = mtm.alloc::<NSView>();
            let v = NSView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(COLUMN_WIDTH, 320.0)),
            );
            // Column stretches vertically with the host; horizontal position
            // + width are driven by `layout_columns`.
            v.setAutoresizingMask(NSAutoresizingMaskOptions::NSViewHeightSizable);
            v
        };

        // Header label (lane name + count).
        let header: Retained<NSTextField> = unsafe {
            let alloc = mtm.alloc::<NSTextField>();
            let t = NSTextField::initWithFrame(
                alloc,
                NSRect::new(
                    NSPoint::new(0.0, 320.0 - COLUMN_HEADER_HEIGHT),
                    NSSize::new(COLUMN_WIDTH, COLUMN_HEADER_HEIGHT),
                ),
            );
            t.setStringValue(&NSString::from_str(lane));
            t.setEditable(false);
            t.setSelectable(false);
            t.setBezeled(false);
            t.setBordered(false);
            t.setDrawsBackground(false);
            t.setFont(Some(&NSFont::boldSystemFontOfSize(12.0)));
            t.setTextColor(Some(&NSColor::labelColor()));
            t.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewMinYMargin,
            );
            t
        };
        unsafe { container.addSubview(&header) };

        // Inline "+ Add task" field just below the header.
        let add_field: Retained<NSTextField> = unsafe {
            let alloc = mtm.alloc::<NSTextField>();
            let t = NSTextField::initWithFrame(
                alloc,
                NSRect::new(
                    NSPoint::new(0.0, 320.0 - COLUMN_HEADER_HEIGHT - ADD_ROW_HEIGHT),
                    NSSize::new(COLUMN_WIDTH, ADD_ROW_HEIGHT - 4.0),
                ),
            );
            t.setPlaceholderString(Some(&NSString::from_str("+ Add task")));
            t.setEditable(true);
            t.setSelectable(true);
            t.setBezeled(true);
            t.setDrawsBackground(true);
            t.setTarget(Some(self.menu_target.as_ref()));
            t.setAction(Some(sel!(handleAddTaskSubmit:)));
            t.setTag(tag as isize);
            t.setAutoresizingMask(
                NSAutoresizingMaskOptions::NSViewWidthSizable
                    | NSAutoresizingMaskOptions::NSViewMinYMargin,
            );
            t
        };
        unsafe { container.addSubview(&add_field) };

        // Scrollable table below.
        let scroll_frame = NSRect::new(
            NSPoint::new(0.0, 0.0),
            NSSize::new(COLUMN_WIDTH, 320.0 - COLUMN_HEADER_HEIGHT - ADD_ROW_HEIGHT),
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

        let data_source: Retained<BoardDataSource> = {
            let ivars = BoardDataSourceIvars {
                inner: self.inner.clone(),
                lane: lane.to_string(),
            };
            let this = mtm.alloc::<BoardDataSource>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };
        let delegate: Retained<BoardDelegate> = {
            let ivars = BoardDelegateIvars {
                inner: self.inner.clone(),
                lane: lane.to_string(),
            };
            let this = mtm.alloc::<BoardDelegate>().set_ivars(ivars);
            unsafe { msg_send_id![super(this), init] }
        };

        let table: Retained<BoardTableView> = {
            let ivars = BoardTableIvars {
                inner: self.inner.clone(),
                menu_target: self.menu_target.clone(),
                lane: lane.to_string(),
            };
            let this = mtm.alloc::<BoardTableView>().set_ivars(ivars);
            let this: Retained<BoardTableView> = unsafe { msg_send_id![super(this), init] };
            unsafe { this.setFrame(scroll_frame) };
            this
        };

        let col_model: Retained<NSTableColumn> = unsafe {
            let alloc = mtm.alloc::<NSTableColumn>();
            msg_send_id![alloc, init]
        };
        unsafe {
            col_model.setWidth(COLUMN_WIDTH);
            table.addTableColumn(&col_model);
            table.setHeaderView(None);
            table.setRowHeight(ROW_HEIGHT);
            let ds_proto = ProtocolObject::from_ref::<BoardDataSource>(&data_source);
            table.setDataSource(Some(ds_proto));
            let del_proto = ProtocolObject::from_ref::<BoardDelegate>(&delegate);
            table.setDelegate(Some(del_proto));
            scroll.setDocumentView(Some(&table));

            // Register the drag type on this column's table. Same-lane
            // drops become `board_reorder_task`; foreign-lane drops become
            // `board_move_task` (the data source's accept_drop decides).
            let pbtype: Retained<NSString> = NSString::from_str(TASK_DRAG_TYPE);
            let types: Retained<objc2_foundation::NSArray<NSString>> =
                objc2_foundation::NSArray::from_vec(vec![pbtype]);
            let _: () = msg_send![&table, registerForDraggedTypes: &*types];
        }
        unsafe { container.addSubview(&scroll) };

        BoardColumnView {
            lane: lane.to_string(),
            container,
            header,
            add_field,
            table,
            data_source,
            delegate,
        }
    }

    /// Set the group filter. Passing `None` shows tasks from all groups.
    /// Not yet wired to a UI widget — exposed for the appkit integration
    /// layer to call based on e.g. the selected agent's group.
    pub fn set_group_filter(&self, group: Option<String>) {
        let mut inner = self.inner.borrow_mut();
        if inner.group_filter == group {
            return;
        }
        inner.group_filter = group;
        inner.signature = String::new();
    }
}
