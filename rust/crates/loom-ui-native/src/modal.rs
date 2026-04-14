//! Reusable modal form dialog.
//!
//! Wraps `NSAlert` with a custom accessory view: a vertical stack of labeled
//! text fields. Used everywhere we need a quick prompt without building a
//! dedicated window — Add Group, Rename, Edit Agent, Add Agent, Add Terminal.
//!
//! `prompt_form` blocks the main run loop (modal) and returns either the
//! submitted field values keyed by `FormField.key`, or `None` if the user
//! cancelled.
//!
//! Sketch-level: fixed 360pt-wide frame-based layout. Autolayout / NSStackView
//! can come later if we want variable-height fields or richer chrome.

use std::collections::HashMap;

use objc2::rc::Retained;
use objc2_app_kit::{NSAlert, NSTextField, NSView};
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize, NSString};

/// One labeled field in a [`prompt_form`] call.
pub struct FormField {
    /// Visible label text shown above the input.
    pub label: String,
    /// Key the field's value is returned under in the result map.
    pub key: String,
    /// Initial value shown when the modal opens.
    pub initial: String,
    /// Optional placeholder shown when the field is empty.
    pub placeholder: Option<String>,
}

impl FormField {
    pub fn new(label: impl Into<String>, key: impl Into<String>) -> Self {
        Self {
            label: label.into(),
            key: key.into(),
            initial: String::new(),
            placeholder: None,
        }
    }
    pub fn with_initial(mut self, v: impl Into<String>) -> Self {
        self.initial = v.into();
        self
    }
    pub fn with_placeholder(mut self, v: impl Into<String>) -> Self {
        self.placeholder = Some(v.into());
        self
    }
}

/// First-button response code from NSAlert. Cocoa constant
/// `NSAlertFirstButtonReturn`.
const ALERT_FIRST_BUTTON: isize = 1000;

pub fn prompt_form(
    mtm: MainThreadMarker,
    title: &str,
    message: Option<&str>,
    fields: Vec<FormField>,
    submit_label: &str,
) -> Option<HashMap<String, String>> {
    if fields.is_empty() {
        return None;
    }
    unsafe {
        let alert: Retained<NSAlert> = {
            let alloc = mtm.alloc::<NSAlert>();
            objc2::msg_send_id![alloc, init]
        };
        alert.setMessageText(&NSString::from_str(title));
        if let Some(msg) = message {
            alert.setInformativeText(&NSString::from_str(msg));
        }
        let _ = alert.addButtonWithTitle(&NSString::from_str(submit_label));
        let _ = alert.addButtonWithTitle(&NSString::from_str("Cancel"));

        // Build the accessory view. Frame-based layout: each row is a
        // 16pt label stacked above a 24pt input, with 8pt of padding below
        // each row (first/last padding baked in by NSAlert).
        let width: f64 = 360.0;
        let row_height: f64 = 52.0;
        let total_height: f64 = (fields.len() as f64) * row_height;

        let host: Retained<NSView> = {
            let alloc = mtm.alloc::<NSView>();
            NSView::initWithFrame(
                alloc,
                NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(width, total_height)),
            )
        };

        let mut inputs: Vec<Retained<NSTextField>> = Vec::with_capacity(fields.len());
        for (i, f) in fields.iter().enumerate() {
            // Topmost row at highest y; rows flow top → bottom as i increases.
            let row_from_bottom = fields.len() - 1 - i;
            let y_base = (row_from_bottom as f64) * row_height;

            let label_frame = NSRect::new(
                NSPoint::new(0.0, y_base + row_height - 20.0),
                NSSize::new(width, 16.0),
            );
            let input_frame = NSRect::new(
                NSPoint::new(0.0, y_base + 4.0),
                NSSize::new(width, 24.0),
            );

            let label: Retained<NSTextField> = {
                let alloc = mtm.alloc::<NSTextField>();
                NSTextField::initWithFrame(alloc, label_frame)
            };
            label.setStringValue(&NSString::from_str(&f.label));
            label.setEditable(false);
            label.setSelectable(false);
            label.setBezeled(false);
            label.setBordered(false);
            label.setDrawsBackground(false);
            host.addSubview(&label);

            let input: Retained<NSTextField> = {
                let alloc = mtm.alloc::<NSTextField>();
                NSTextField::initWithFrame(alloc, input_frame)
            };
            input.setStringValue(&NSString::from_str(&f.initial));
            if let Some(ph) = &f.placeholder {
                input.setPlaceholderString(Some(&NSString::from_str(ph)));
            }
            input.setEditable(true);
            input.setSelectable(true);
            input.setBezeled(true);
            input.setDrawsBackground(true);
            host.addSubview(&input);
            inputs.push(input);
        }

        alert.setAccessoryView(Some(&host));

        let response: isize = alert.runModal();
        if response != ALERT_FIRST_BUTTON {
            return None;
        }

        let mut out = HashMap::with_capacity(fields.len());
        for (f, input) in fields.iter().zip(inputs.iter()) {
            let v = input.stringValue().to_string();
            out.insert(f.key.clone(), v);
        }
        Some(out)
    }
}

/// Confirm-only modal. Returns `true` if the user clicked the confirm button.
/// Used for destructive actions (Remove group / agent / terminal).
pub fn confirm(
    mtm: MainThreadMarker,
    title: &str,
    message: Option<&str>,
    confirm_label: &str,
) -> bool {
    unsafe {
        let alert: Retained<NSAlert> = {
            let alloc = mtm.alloc::<NSAlert>();
            objc2::msg_send_id![alloc, init]
        };
        alert.setMessageText(&NSString::from_str(title));
        if let Some(msg) = message {
            alert.setInformativeText(&NSString::from_str(msg));
        }
        let _ = alert.addButtonWithTitle(&NSString::from_str(confirm_label));
        let _ = alert.addButtonWithTitle(&NSString::from_str("Cancel"));
        alert.runModal() == ALERT_FIRST_BUTTON
    }
}
