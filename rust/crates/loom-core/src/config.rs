//! Runtime paths, env vars, port. Mirrors `loom/config.py`.

use std::path::PathBuf;

/// The default HTTP port the engine listens on.
pub const DEFAULT_PORT: u16 = 18932;

/// Environment variable that overrides the default boot command for new agents.
pub const ENV_DEFAULT_CMD: &str = "LOOM_DEFAULT_CMD";

/// Env var pointing at the standalone install dir (used when the app self-relaunches).
pub const ENV_INSTALL_DIR: &str = "LOOM_INSTALL_DIR";

/// Environment variable carrying the owning cell ID for spawned processes.
pub const ENV_CELL_ID: &str = "LOOM_CELL_ID";

/// Return the directory where persistent app data lives.
///
/// On macOS: `~/Library/Application Support/Loom`.
/// Intentionally distinct from Python Loom's path
/// (`~/Library/Application Support/iTerm2/Scripts/loom/loom`) to avoid DB collisions.
pub fn data_dir() -> PathBuf {
    if let Ok(custom) = std::env::var(ENV_INSTALL_DIR) {
        return PathBuf::from(custom);
    }
    if let Some(base) = dirs::data_dir() {
        return base.join("Loom");
    }
    // Fallback: current dir
    PathBuf::from(".loom-data")
}

pub fn db_path() -> PathBuf {
    data_dir().join("loom.db")
}

pub fn attachments_dir() -> PathBuf {
    data_dir().join("attachments")
}

pub fn log_path() -> PathBuf {
    data_dir().join("loom.log")
}

pub fn default_command() -> String {
    std::env::var(ENV_DEFAULT_CMD).unwrap_or_else(|_| "claude".to_string())
}

/// Ensure the data dir exists. Call on startup.
pub fn ensure_data_dir() -> std::io::Result<()> {
    let dir = data_dir();
    std::fs::create_dir_all(&dir)?;
    std::fs::create_dir_all(attachments_dir())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // Serialize env-mutating tests — without this, parallel execution races
    // (`remove_var` in one test while another has just `set_var`ed).
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn data_dir_respects_override() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var(ENV_INSTALL_DIR, "/tmp/loom-test-data");
        assert_eq!(data_dir(), PathBuf::from("/tmp/loom-test-data"));
        std::env::remove_var(ENV_INSTALL_DIR);
    }

    #[test]
    fn default_command_falls_back_to_claude() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var(ENV_DEFAULT_CMD);
        assert_eq!(default_command(), "claude");
    }

    #[test]
    fn default_command_respects_env() {
        let _g = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var(ENV_DEFAULT_CMD, "codex");
        assert_eq!(default_command(), "codex");
        std::env::remove_var(ENV_DEFAULT_CMD);
    }
}
