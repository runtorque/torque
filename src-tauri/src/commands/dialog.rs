use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

fn normalized_label(value: Option<String>, fallback: &str) -> String {
    value
        .map(|label| label.trim().chars().take(48).collect::<String>())
        .filter(|label| !label.is_empty())
        .unwrap_or_else(|| fallback.to_string())
}

#[tauri::command]
pub async fn confirm(
    app: AppHandle,
    message: String,
    title: Option<String>,
    confirm_label: Option<String>,
    cancel_label: Option<String>,
    destructive: Option<bool>,
) -> Result<bool, String> {
    let title = normalized_label(title, "Torque");
    let confirm_label = normalized_label(confirm_label, "Continue");
    let cancel_label = normalized_label(cancel_label, "Cancel");
    let mut dialog =
        app.dialog()
            .message(message)
            .title(title)
            .buttons(MessageDialogButtons::OkCancelCustom(
                confirm_label,
                cancel_label,
            ));
    if destructive.unwrap_or(false) {
        dialog = dialog.kind(MessageDialogKind::Warning);
    }
    tauri::async_runtime::spawn_blocking(move || dialog.blocking_show())
        .await
        .map_err(|error| format!("Native confirmation failed: {error}"))
}

#[cfg(test)]
mod tests {
    use super::normalized_label;

    #[test]
    fn dialog_labels_are_trimmed_bounded_and_have_fallbacks() {
        assert_eq!(
            normalized_label(Some("  Delete  ".into()), "Continue"),
            "Delete"
        );
        assert_eq!(normalized_label(Some("  ".into()), "Continue"), "Continue");
        assert_eq!(normalized_label(Some("x".repeat(80)), "Continue").len(), 48);
    }
}
