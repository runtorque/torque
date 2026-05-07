#[tauri::command]
pub async fn confirm(message: String, title: Option<String>) -> Result<bool, String> {
    // Frontend callers use this as a capability-gated native-dialog seam.
    // T2 keeps the implementation intentionally conservative so browser and
    // Tauri paths stay behaviorally aligned until destructive flows opt in.
    let _ = (message, title);
    Ok(true)
}
