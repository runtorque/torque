//! Action manager — discovers, loads, and renders Loom actions.
//!
//! Actions are YAML files under `.loom/actions/` (project scope) or
//! `~/.loom/actions/` (user scope). Full port of `loom/actions.py`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::render::{render_prompt, RenderError};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionScope {
    Project,
    User,
}

impl ActionScope {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Project => "project",
            Self::User => "user",
        }
    }
}

/// Metadata describing an action on disk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionInfo {
    pub name: String,
    pub scope: ActionScope,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub group: String,
    #[serde(default)]
    pub labels: Vec<String>,
    #[serde(default)]
    pub worktree: bool,
    #[serde(default)]
    pub terminals: Vec<serde_json::Value>,
    #[serde(default)]
    pub transitions: Vec<Transition>,
    #[serde(default)]
    pub max_depth: Option<i32>,
    #[serde(default)]
    pub variables: Vec<ActionVariable>,
    #[serde(default)]
    pub prompt: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transition {
    pub action: String,
    #[serde(default)]
    pub when: String,
    #[serde(default)]
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionVariable {
    pub name: String,
    #[serde(default)]
    pub default: Option<serde_json::Value>,
    #[serde(default)]
    pub required: bool,
}

#[derive(Debug, Error)]
pub enum ActionError {
    #[error("action not found: {0}")]
    NotFound(String),

    #[error("io: {0}")]
    Io(#[from] std::io::Error),

    #[error("yaml: {0}")]
    Yaml(#[from] serde_yaml::Error),

    #[error("render: {0}")]
    Render(#[from] RenderError),

    #[error("validation: {0}")]
    Validation(String),
}

/// Primary interface. Holds the search roots; loads lazily.
pub struct ActionManager {
    project_root: Option<PathBuf>,
    user_root: Option<PathBuf>,
}

impl ActionManager {
    pub fn new(project_root: Option<PathBuf>, user_root: Option<PathBuf>) -> Self {
        Self { project_root, user_root }
    }

    pub fn with_project_root(root: PathBuf) -> Self {
        Self::new(Some(root), default_user_root())
    }

    /// Enumerate all actions across both scopes. Project actions shadow user
    /// actions with the same name.
    pub fn list_actions(&self) -> Result<Vec<ActionInfo>, ActionError> {
        let mut seen: HashMap<String, ActionInfo> = HashMap::new();
        // user first, project overrides
        if let Some(root) = &self.user_root {
            for info in walk_actions(root, ActionScope::User)? {
                seen.insert(info.name.clone(), info);
            }
        }
        if let Some(root) = &self.project_root {
            for info in walk_actions(root, ActionScope::Project)? {
                seen.insert(info.name.clone(), info);
            }
        }
        let mut out: Vec<ActionInfo> = seen.into_values().collect();
        out.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(out)
    }

    /// Resolve an action by name across both scopes (project wins).
    pub fn get_action(&self, name: &str) -> Result<ActionInfo, ActionError> {
        if let Some(root) = &self.project_root {
            if let Some(info) = load_action(root, name, ActionScope::Project)? {
                return Ok(info);
            }
        }
        if let Some(root) = &self.user_root {
            if let Some(info) = load_action(root, name, ActionScope::User)? {
                return Ok(info);
            }
        }
        Err(ActionError::NotFound(name.to_string()))
    }

    /// Render an action into its final prompt text.
    pub fn render(
        &self,
        name: &str,
        vars: &serde_json::Map<String, serde_json::Value>,
        loom_ctx: &serde_json::Value,
    ) -> Result<String, ActionError> {
        let info = self.get_action(name)?;
        validate_prompt(&info.prompt)?;
        Ok(render_prompt(&info.prompt, vars, loom_ctx)?)
    }
}

fn default_user_root() -> Option<PathBuf> {
    dirs::home_dir().map(|h| h.join(".loom").join("actions"))
}

/// Walk a root directory and collect all `.yaml`/`.yml` action files.
/// Subdirectories become name prefixes with `/`.
fn walk_actions(root: &Path, scope: ActionScope) -> Result<Vec<ActionInfo>, ActionError> {
    let mut out = Vec::new();
    if !root.exists() {
        return Ok(out);
    }
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e.into()),
        };
        for entry in entries {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let is_yaml = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e == "yaml" || e == "yml")
                .unwrap_or(false);
            if !is_yaml {
                continue;
            }
            let rel = path.strip_prefix(root).unwrap();
            let name = rel
                .with_extension("")
                .to_string_lossy()
                .replace(std::path::MAIN_SEPARATOR, "/");
            match parse_action_file(&path, &name, scope) {
                Ok(info) => out.push(info),
                Err(err) => tracing::warn!("failed to parse action {:?}: {err}", path),
            }
        }
    }
    Ok(out)
}

fn load_action(
    root: &Path,
    name: &str,
    scope: ActionScope,
) -> Result<Option<ActionInfo>, ActionError> {
    for ext in &["yaml", "yml"] {
        let path = root.join(format!("{name}.{ext}"));
        if path.exists() {
            return Ok(Some(parse_action_file(&path, name, scope)?));
        }
    }
    Ok(None)
}

fn parse_action_file(
    path: &Path,
    name: &str,
    scope: ActionScope,
) -> Result<ActionInfo, ActionError> {
    let raw = std::fs::read_to_string(path)?;
    let mut value: serde_yaml::Value = serde_yaml::from_str(&raw)?;
    // Legacy format: coalesce task/instructions/context/criteria → prompt.
    coalesce_legacy_prompt(&mut value);

    let raw_info: RawAction = serde_yaml::from_value(value).unwrap_or_default();

    let variables = if raw_info.prompt.is_empty() {
        Vec::new()
    } else {
        crate::render::discover_variables(&raw_info.prompt)
    };

    Ok(ActionInfo {
        name: name.to_string(),
        scope,
        path: path.to_string_lossy().to_string(),
        group: raw_info.group.unwrap_or_default(),
        labels: raw_info.labels.unwrap_or_default(),
        worktree: raw_info.worktree.unwrap_or_default(),
        terminals: raw_info.terminals.unwrap_or_default(),
        transitions: raw_info.transitions.unwrap_or_default(),
        max_depth: raw_info.max_depth,
        variables,
        prompt: raw_info.prompt,
    })
}

fn coalesce_legacy_prompt(value: &mut serde_yaml::Value) {
    let Some(map) = value.as_mapping_mut() else { return };
    let has_new = map.contains_key(serde_yaml::Value::String("prompt".into()));
    if has_new {
        return;
    }
    let mut parts: Vec<String> = Vec::new();
    for field in ["task", "instructions", "context", "criteria"] {
        if let Some(v) = map.get(serde_yaml::Value::String(field.into())) {
            if let Some(s) = v.as_str() {
                if !s.trim().is_empty() {
                    parts.push(s.trim().to_string());
                }
            }
        }
    }
    if !parts.is_empty() {
        map.insert(
            serde_yaml::Value::String("prompt".into()),
            serde_yaml::Value::String(parts.join("\n\n")),
        );
    }
}

#[derive(Debug, Deserialize, Default)]
struct RawAction {
    #[serde(default)]
    prompt: String,
    #[serde(default)]
    group: Option<String>,
    #[serde(default)]
    labels: Option<Vec<String>>,
    #[serde(default)]
    worktree: Option<bool>,
    #[serde(default)]
    terminals: Option<Vec<serde_json::Value>>,
    #[serde(default)]
    transitions: Option<Vec<Transition>>,
    #[serde(default)]
    max_depth: Option<i32>,
}

/// Every action must reference {{ TASK }} at least once.
pub fn validate_prompt(prompt: &str) -> Result<(), ActionError> {
    if !prompt.contains("TASK") {
        return Err(ActionError::Validation(
            "action prompt must reference {{ TASK }}".into(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::tempdir;

    fn write_action(dir: &Path, rel: &str, body: &str) {
        let path = dir.join(rel);
        if let Some(p) = path.parent() {
            std::fs::create_dir_all(p).unwrap();
        }
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(body.as_bytes()).unwrap();
    }

    #[test]
    fn list_actions_finds_nested() {
        let tmp = tempdir().unwrap();
        write_action(tmp.path(), "foo.yaml", "prompt: 'do {{ TASK }}'\n");
        write_action(tmp.path(), "feature/bar.yaml", "prompt: '{{ TASK }}'\n");
        let mgr = ActionManager::new(Some(tmp.path().into()), None);
        let actions = mgr.list_actions().unwrap();
        let names: Vec<&str> = actions.iter().map(|a| a.name.as_str()).collect();
        assert!(names.contains(&"foo"));
        assert!(names.contains(&"feature/bar"));
    }

    #[test]
    fn project_overrides_user() {
        let user = tempdir().unwrap();
        let proj = tempdir().unwrap();
        write_action(user.path(), "shared.yaml", "prompt: 'user {{ TASK }}'\n");
        write_action(proj.path(), "shared.yaml", "prompt: 'proj {{ TASK }}'\n");
        let mgr = ActionManager::new(Some(proj.path().into()), Some(user.path().into()));
        let info = mgr.get_action("shared").unwrap();
        assert_eq!(info.scope, ActionScope::Project);
        assert!(info.prompt.contains("proj"));
    }

    #[test]
    fn legacy_format_coalesces_to_prompt() {
        let tmp = tempdir().unwrap();
        write_action(
            tmp.path(),
            "old.yaml",
            "task: 'Do {{ TASK }}'\ninstructions: 'Be careful'\n",
        );
        let mgr = ActionManager::new(Some(tmp.path().into()), None);
        let info = mgr.get_action("old").unwrap();
        assert!(info.prompt.contains("Do {{ TASK }}"));
        assert!(info.prompt.contains("Be careful"));
    }

    #[test]
    fn validate_rejects_prompt_without_task() {
        assert!(validate_prompt("no variable here").is_err());
        assert!(validate_prompt("hello {{ TASK }}").is_ok());
    }
}
