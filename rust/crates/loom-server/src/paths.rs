use std::path::PathBuf;

/// Expand a leading `~` to the current user's home directory, matching the
/// Python backend's widespread `os.path.expanduser(...)` behavior.
pub fn expand_user_path(path: &str) -> PathBuf {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return PathBuf::new();
    }
    if trimmed == "~" {
        return dirs::home_dir().unwrap_or_else(|| PathBuf::from(trimmed));
    }
    if let Some(rest) = trimmed.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest);
        }
    }
    if let Some(rest) = trimmed.strip_prefix("~\\") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest);
        }
    }
    PathBuf::from(trimmed)
}

pub fn expand_user_path_string(path: &str) -> String {
    expand_user_path(path).to_string_lossy().to_string()
}

pub fn canonical_user_path(path: &str) -> String {
    let candidate = expand_user_path(path);
    std::fs::canonicalize(&candidate)
        .unwrap_or(candidate)
        .to_string_lossy()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expand_user_path_expands_home_prefix() {
        let home = dirs::home_dir().expect("home directory should exist in tests");
        assert_eq!(expand_user_path("~/tmp"), home.join("tmp"));
    }

    #[test]
    fn expand_user_path_leaves_absolute_paths_alone() {
        assert_eq!(expand_user_path("/tmp/x"), PathBuf::from("/tmp/x"));
    }
}
