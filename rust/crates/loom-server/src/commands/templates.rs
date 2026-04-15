//! Agent-template CRUD + render.
//!
//! Templates are YAML bundles of defaults for spawning an agent (provider,
//! command, model, system_prompt, initial_prompt, worktree config, env, child
//! terminals). Stored in `.loom/agents/<name>.yaml` (project scope) or
//! `~/.loom/agents/<name>.yaml` (user scope). Project shadows user on name
//! collision.
//!
//! `render_template` resolves a named template + an overrides dict into a
//! single merged config the UI can show or dispatch can consume.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use super::{optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn list_templates(_ctx: &CmdContext, _req: &Value) -> CmdResult {
    let mut templates: Vec<Value> = Vec::new();
    let mut seen_names: std::collections::HashSet<String> = std::collections::HashSet::new();

    // Project first, user second — project wins on name clash.
    for (root, scope) in [
        (project_templates_root(), "project"),
        (Some(user_templates_root()), "user"),
    ] {
        let Some(root) = root else { continue };
        if !root.exists() {
            continue;
        }
        let names = list_yaml_names(&root);
        for name in names {
            if seen_names.insert(name.clone()) {
                templates.push(json!({
                    "name": name,
                    "scope": scope,
                }));
            } else {
                // A project template with the same name already claimed this
                // slot — skip the user variant.
            }
        }
    }
    templates.sort_by(|a, b| {
        a["name"]
            .as_str()
            .unwrap_or("")
            .cmp(b["name"].as_str().unwrap_or(""))
    });
    Ok(json!({ "templates": templates }))
}

pub async fn get_template(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?;
    let (path, scope) = resolve_path(name)
        .ok_or_else(|| CmdError::BadRequest(format!("template '{name}' not found")))?;
    let raw =
        std::fs::read_to_string(&path).map_err(|e| CmdError::BadRequest(format!("read: {e}")))?;
    let parsed: Value =
        serde_yaml::from_str(&raw).map_err(|e| CmdError::BadRequest(format!("yaml parse: {e}")))?;
    Ok(json!({
        "name": name,
        "scope": scope,
        "path": path.to_string_lossy(),
        "config": parsed,
        "raw": raw,
    }))
}

pub async fn save_template(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    if !is_safe_name(&name) {
        return Err(CmdError::BadRequest(format!(
            "invalid template name '{name}' (letters, digits, -, _, / only)"
        )));
    }
    let scope = optional_str(req, "scope").unwrap_or("project");
    let root = match scope {
        "user" => user_templates_root(),
        _ => project_templates_root()
            .ok_or_else(|| CmdError::BadRequest("no project root".into()))?,
    };

    // Caller sends either `config` (an object we serialize to YAML) or `raw`
    // (pre-serialized YAML text). We prefer `raw` when present to preserve
    // user formatting / comments.
    let yaml_text: String = if let Some(raw) = req.get("raw").and_then(|v| v.as_str()) {
        // Validate it parses.
        let _: serde_yaml::Value = serde_yaml::from_str(raw)
            .map_err(|e| CmdError::BadRequest(format!("yaml parse: {e}")))?;
        raw.to_string()
    } else if let Some(cfg) = req.get("config") {
        serde_yaml::to_string(cfg).map_err(|e| CmdError::BadRequest(format!("yaml encode: {e}")))?
    } else {
        return Err(CmdError::BadRequest(
            "missing 'config' (object) or 'raw' (yaml string)".into(),
        ));
    };

    let path = root.join(format!("{name}.yaml"));
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| CmdError::BadRequest(format!("mkdir: {e}")))?;
    }
    std::fs::write(&path, yaml_text).map_err(|e| CmdError::BadRequest(format!("write: {e}")))?;

    Ok(json!({ "ok": true, "name": name, "scope": scope, "path": path.to_string_lossy() }))
}

pub async fn delete_template(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let scope = optional_str(req, "scope").unwrap_or("project");
    let root = match scope {
        "user" => user_templates_root(),
        _ => project_templates_root()
            .ok_or_else(|| CmdError::BadRequest("no project root".into()))?,
    };
    let mut removed = false;
    for ext in &["yaml", "yml"] {
        let path = root.join(format!("{name}.{ext}"));
        if path.exists() {
            std::fs::remove_file(&path)
                .map_err(|e| CmdError::BadRequest(format!("remove: {e}")))?;
            removed = true;
        }
    }
    if !removed {
        return Err(CmdError::BadRequest(format!("template '{name}' not found")));
    }
    Ok(json!({ "ok": true }))
}

/// Merge a template + overrides into a single config dict the UI + dispatch
/// can consume. Merge precedence (highest wins): overrides → template.
/// `group` is currently advisory — returned in the response but not used in
/// the merge until group_settings-level defaults are wired.
pub async fn render_template(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let overrides = req.get("overrides").cloned().unwrap_or(Value::Null);
    let group = optional_str(req, "group").unwrap_or("").to_string();

    let (path, _scope) = resolve_path(&name)
        .ok_or_else(|| CmdError::BadRequest(format!("template '{name}' not found")))?;
    let raw =
        std::fs::read_to_string(&path).map_err(|e| CmdError::BadRequest(format!("read: {e}")))?;
    let mut template: Value =
        serde_yaml::from_str(&raw).map_err(|e| CmdError::BadRequest(format!("yaml: {e}")))?;

    // Deep-merge overrides onto the template.
    if let Some(over_obj) = overrides.as_object() {
        merge_object(&mut template, over_obj.clone());
    }

    Ok(json!({
        "type": "template_rendered",
        "name": name,
        "group": group,
        "config": template,
    }))
}

// -----
// helpers
// -----

fn list_yaml_names(root: &Path) -> Vec<String> {
    fn walk(dir: &Path, base: &Path, out: &mut Vec<String>) {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, base, out);
            } else if path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e == "yaml" || e == "yml")
                .unwrap_or(false)
            {
                if let Ok(rel) = path.strip_prefix(base) {
                    let mut name = rel.with_extension("").to_string_lossy().replace('\\', "/");
                    // Cosmetic: normalize leading "./"
                    if let Some(stripped) = name.strip_prefix("./") {
                        name = stripped.to_string();
                    }
                    out.push(name);
                }
            }
        }
    }
    let mut out = Vec::new();
    walk(root, root, &mut out);
    out
}

fn resolve_path(name: &str) -> Option<(PathBuf, &'static str)> {
    if let Some(root) = project_templates_root() {
        for ext in &["yaml", "yml"] {
            let p = root.join(format!("{name}.{ext}"));
            if p.exists() {
                return Some((p, "project"));
            }
        }
    }
    let root = user_templates_root();
    for ext in &["yaml", "yml"] {
        let p = root.join(format!("{name}.{ext}"));
        if p.exists() {
            return Some((p, "user"));
        }
    }
    None
}

fn project_templates_root() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("LOOM_PROJECT_ROOT") {
        return Some(PathBuf::from(root).join(".loom").join("agents"));
    }
    std::env::current_dir()
        .ok()
        .map(|d| d.join(".loom").join("agents"))
}

fn user_templates_root() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".loom")
        .join("agents")
}

fn is_safe_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with('/')
        && !name.contains("..")
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '/'))
}

/// Deep-merge a JSON object into a JSON value. Object keys recurse; scalars +
/// arrays get replaced wholesale.
fn merge_object(target: &mut Value, patch: serde_json::Map<String, Value>) {
    if !target.is_object() {
        *target = Value::Object(Default::default());
    }
    let target_obj = target.as_object_mut().unwrap();
    for (k, v) in patch {
        match (target_obj.get_mut(&k), v) {
            (Some(existing), Value::Object(inner)) if existing.is_object() => {
                merge_object(existing, inner);
            }
            (_, other) => {
                target_obj.insert(k, other);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_safe_name_accepts_subdirs() {
        assert!(is_safe_name("claude/default"));
        assert!(is_safe_name("my-agent"));
        assert!(is_safe_name("my_agent_1"));
    }

    #[test]
    fn is_safe_name_rejects_traversal() {
        assert!(!is_safe_name("../etc"));
        assert!(!is_safe_name("/root"));
        assert!(!is_safe_name(""));
        assert!(!is_safe_name("has space"));
        assert!(!is_safe_name("has.dot"));
    }

    #[test]
    fn merge_object_deep_merges() {
        let mut base = json!({
            "provider": "claude",
            "worktree": {
                "enabled": true,
                "base": "main"
            }
        });
        let patch = serde_json::from_value(json!({
            "worktree": {
                "base": "develop",
                "squash": true
            },
            "model": "opus"
        }))
        .unwrap();
        merge_object(&mut base, patch);
        assert_eq!(base["provider"], "claude");
        assert_eq!(base["model"], "opus");
        assert_eq!(base["worktree"]["enabled"], true);
        assert_eq!(base["worktree"]["base"], "develop");
        assert_eq!(base["worktree"]["squash"], true);
    }

    #[test]
    fn merge_replaces_scalar_with_object_and_vice_versa() {
        let mut base = json!({"a": "scalar"});
        let patch = serde_json::from_value(json!({"a": {"nested": 1}})).unwrap();
        merge_object(&mut base, patch);
        assert_eq!(base["a"]["nested"], 1);
    }
}
