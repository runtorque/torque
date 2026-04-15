//! Action CRUD + render commands.
//!
//! Ports the server-side `list_actions`, `get_action`, `render_action`,
//! `save_action`, `delete_action`, `preview_prompt`, `discover_pipelines`.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;

use serde_json::{json, Value};
use serde_yaml::Mapping as YamlMap;

use loom_actions::manager::{ActionError, ActionInfo, ActionManager, ActionScope};
use loom_actions::{context::stub_context, context::LoomContextBuilder};

use super::{ok, optional_str, required_str, CmdContext, CmdError, CmdResult};

pub async fn list_actions(ctx: &CmdContext, _req: &Value) -> CmdResult {
    let mgr = build_manager(ctx).await?;
    let actions = mgr.list_actions().map_err(|e| action_err(e))?;
    Ok(json!({ "actions": actions }))
}

pub async fn get_action(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?;
    let mgr = build_manager(ctx).await?;
    let info = mgr.get_action(name).map_err(action_err)?;
    Ok(serde_json::to_value(&info)?)
}

pub async fn render_action(ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?;
    let vars = req
        .get("vars")
        .and_then(|v| v.as_object())
        .cloned()
        .unwrap_or_default();

    let mgr = build_manager(ctx).await?;
    let info = mgr.get_action(name).map_err(action_err)?;

    // Build a stub `loom` ctx for preview. Full context is used during dispatch.
    let loom_ctx = stub_context();

    // Add TASK placeholder if caller didn't provide one.
    let mut vars = vars;
    vars.entry("TASK".to_string())
        .or_insert_with(|| Value::String("<TASK>".into()));

    let rendered = loom_actions::render::render_prompt(&info.prompt, &vars, &loom_ctx)
        .map_err(|e| CmdError::BadRequest(format!("render: {e}")))?;

    Ok(json!({
        "action": info,
        "rendered": rendered,
    }))
}

pub async fn preview_prompt(ctx: &CmdContext, req: &Value) -> CmdResult {
    let task_id = required_str(req, "task_id")?.to_string();
    let (task, agent) = {
        let st = ctx.state.lock().await;
        let task = st
            .board_tasks
            .get(&task_id)
            .cloned()
            .ok_or_else(|| CmdError::BadRequest(format!("task '{task_id}' not found")))?;
        let agent = if task.agent_id.is_empty() {
            None
        } else {
            st.agents.get(&task.agent_id).cloned()
        };
        (task, agent)
    };
    if task.action_name.is_empty() {
        return Err(CmdError::BadRequest("task has no action".into()));
    }

    let mgr = build_manager(ctx).await?;
    let info = mgr.get_action(&task.action_name).map_err(action_err)?;

    let mut vars = task.action_vars.clone();
    vars.insert("TASK".into(), Value::String(task.task.clone()));

    let state_guard = ctx.state.lock().await;
    let loom_ctx = match agent.as_ref() {
        Some(a) => LoomContextBuilder::new(&state_guard)
            .agent(a)
            .task(&task)
            .build(),
        None => LoomContextBuilder::new(&state_guard).task(&task).build(),
    };
    drop(state_guard);

    let rendered = loom_actions::render::render_prompt(&info.prompt, &vars, &loom_ctx)
        .map_err(|e| CmdError::BadRequest(format!("render: {e}")))?;

    Ok(json!({
        "task_id": task.id,
        "action": info.name,
        "rendered": rendered,
    }))
}

pub async fn save_action(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let scope = optional_str(req, "scope").unwrap_or("project");
    let prompt = required_str(req, "prompt")?.to_string();

    loom_actions::manager::validate_prompt(&prompt).map_err(|e| action_err(e))?;

    // Build the YAML body.
    let mut map = YamlMap::new();
    map.insert(
        serde_yaml::Value::String("prompt".into()),
        serde_yaml::Value::String(prompt),
    );
    if let Some(group) = optional_str(req, "group") {
        map.insert("group".into(), group.into());
    }
    if let Some(labels) = req.get("labels").and_then(|v| v.as_array()) {
        let arr: Vec<serde_yaml::Value> = labels
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.into()))
            .collect();
        map.insert("labels".into(), serde_yaml::Value::Sequence(arr));
    }
    if let Some(worktree) = req.get("worktree").and_then(|v| v.as_bool()) {
        map.insert("worktree".into(), serde_yaml::Value::Bool(worktree));
    }
    if let Some(transitions) = req.get("transitions") {
        let yaml_v =
            serde_yaml::to_value(transitions).map_err(|e| CmdError::BadRequest(e.to_string()))?;
        map.insert("transitions".into(), yaml_v);
    }
    if let Some(md) = req.get("max_depth").and_then(|v| v.as_i64()) {
        map.insert(
            "max_depth".into(),
            serde_yaml::Value::Number(serde_yaml::Number::from(md)),
        );
    }

    let text = serde_yaml::to_string(&serde_yaml::Value::Mapping(map))
        .map_err(|e| CmdError::BadRequest(e.to_string()))?;

    let root = match scope {
        "user" => user_actions_root(),
        _ => {
            project_actions_root().ok_or_else(|| CmdError::BadRequest("no project root".into()))?
        }
    };
    let path = root.join(format!("{name}.yaml"));
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| CmdError::BadRequest(e.to_string()))?;
    }
    std::fs::write(&path, text).map_err(|e| CmdError::BadRequest(e.to_string()))?;

    Ok(json!({ "ok": true, "path": path.to_string_lossy() }))
}

pub async fn delete_action(_ctx: &CmdContext, req: &Value) -> CmdResult {
    let name = required_str(req, "name")?.to_string();
    let scope = optional_str(req, "scope").unwrap_or("project");
    let root = match scope {
        "user" => user_actions_root(),
        _ => {
            project_actions_root().ok_or_else(|| CmdError::BadRequest("no project root".into()))?
        }
    };

    let mut found = false;
    for ext in &["yaml", "yml"] {
        let path = root.join(format!("{name}.{ext}"));
        if path.exists() {
            std::fs::remove_file(&path).map_err(|e| CmdError::BadRequest(e.to_string()))?;
            found = true;
        }
    }
    if !found {
        return Err(CmdError::BadRequest(format!("action '{name}' not found")));
    }
    ok()
}

/// Discover pipelines: connected components in the action transition graph.
pub async fn discover_pipelines(ctx: &CmdContext, _req: &Value) -> CmdResult {
    let mgr = build_manager(ctx).await?;
    let actions = mgr.list_actions().map_err(action_err)?;

    // Build adjacency.
    let mut adj: HashMap<String, HashSet<String>> = HashMap::new();
    let mut all_nodes: HashSet<String> = HashSet::new();
    for info in &actions {
        all_nodes.insert(info.name.clone());
        for t in &info.transitions {
            all_nodes.insert(t.action.clone());
            adj.entry(info.name.clone())
                .or_default()
                .insert(t.action.clone());
            // bidirectional for connected-components analysis
            adj.entry(t.action.clone())
                .or_default()
                .insert(info.name.clone());
        }
    }

    // Connected components via BFS.
    let mut visited: HashSet<String> = HashSet::new();
    let mut pipelines: Vec<Vec<String>> = Vec::new();

    for node in &all_nodes {
        if visited.contains(node) {
            continue;
        }
        let mut component: Vec<String> = Vec::new();
        let mut q: VecDeque<String> = VecDeque::new();
        q.push_back(node.clone());
        visited.insert(node.clone());
        while let Some(cur) = q.pop_front() {
            component.push(cur.clone());
            if let Some(neighbors) = adj.get(&cur) {
                for n in neighbors {
                    if visited.insert(n.clone()) {
                        q.push_back(n.clone());
                    }
                }
            }
        }
        if component.len() > 1 {
            component.sort();
            pipelines.push(component);
        }
    }

    // Include transition edges for UI rendering.
    let edges: Vec<Value> = actions
        .iter()
        .flat_map(|info| {
            info.transitions.iter().map(move |t| {
                json!({
                    "from": info.name,
                    "to": t.action,
                    "when": t.when,
                    "status": t.status,
                })
            })
        })
        .collect();

    Ok(json!({
        "pipelines": pipelines,
        "edges": edges,
        "actions": actions.iter().map(|a| &a.name).collect::<Vec<_>>(),
    }))
}

// ---- helpers --------------------------------------------------------------

fn project_actions_root() -> Option<PathBuf> {
    project_actions_root_pub()
}

/// Exposed for sibling modules (e.g. dispatch.rs) that need the same path.
pub fn project_actions_root_pub() -> Option<PathBuf> {
    if let Ok(root) = std::env::var("LOOM_PROJECT_ROOT") {
        return Some(PathBuf::from(root).join(".loom").join("actions"));
    }
    std::env::current_dir()
        .ok()
        .map(|d| d.join(".loom").join("actions"))
}

fn user_actions_root() -> PathBuf {
    user_actions_root_pub()
}

pub fn user_actions_root_pub() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".loom")
        .join("actions")
}

async fn build_manager(_ctx: &CmdContext) -> Result<ActionManager, CmdError> {
    let project = project_actions_root();
    let user = Some(user_actions_root());
    Ok(ActionManager::new(project, user))
}

fn action_err(err: ActionError) -> CmdError {
    match err {
        ActionError::NotFound(name) => CmdError::BadRequest(format!("action not found: {name}")),
        ActionError::Validation(msg) => CmdError::BadRequest(format!("validation: {msg}")),
        other => CmdError::BadRequest(other.to_string()),
    }
}

// suppress unused warnings for ActionScope if the scope field isn't deserialized.
#[allow(dead_code)]
fn _scope(_s: &str) -> ActionScope {
    ActionScope::Project
}
#[allow(dead_code)]
fn _info_sort(infos: &mut Vec<ActionInfo>) {
    infos.sort_by(|a, b| a.name.cmp(&b.name));
}
