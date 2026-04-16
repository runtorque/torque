//! Build the `loom.*` context namespace for action rendering.
//!
//! Mirrors `_build_loom_context` in `loom/server.py`. Provides safe defaults
//! for preview renders (`LOOM_CONTEXT_STUB`).

use serde_json::{json, Value};

use loom_core::artifacts::{normalize_artifacts, normalize_attachments};
use loom_core::state::{AgentCell, BoardTask, MatrixState};

/// Stub context for preview/discovery renders. Matches Python's
/// `LOOM_CONTEXT_STUB`.
pub fn stub_context() -> Value {
    json!({
        "agent": {
            "id": "preview",
            "name": "Preview",
            "slug": "preview",
            "type": "",
            "group": "Preview",
            "directory": "",
            "worktree_path": "",
            "worktree_branch": "",
        },
        "context": {
            "is_clean": true,
            "tasks_dispatched": 0,
            "previous_tasks": [],
        },
        "worktree": {
            "active": false,
            "path": "",
            "branch": "",
            "base_branch": "",
            "repo_root": "",
            "dirty": false,
            "diff": {},
            "checkpoints": 0,
        },
        "task": {
            "id": "",
            "slug": "",
            "title": "",
            "description": "",
            "depth": 0,
            "is_derived": false,
            "parent_task_id": "",
            "parent_agent_id": "",
            "parent_agent_name": "",
            "parent_agent_slug": "",
            "labels": [],
            "group": "",
            "status": "",
            "verification_mode": "",
            "verification_state": "",
            "verification_notes": "",
            "verification_updated_at": "",
            "verification_updated_by": "",
            "verification_summary": {},
            "worktree_boundary": {},
            "resume_after_boundary_task_id": "",
            "attachments": [],
            "artifacts": [],
            "upstream_artifacts": [],
        },
        "terminals": [],
    })
}

pub struct LoomContextBuilder<'a> {
    state: &'a MatrixState,
    agent: Option<&'a AgentCell>,
    task: Option<&'a BoardTask>,
    previous_tasks: Vec<&'a BoardTask>,
}

impl<'a> LoomContextBuilder<'a> {
    pub fn new(state: &'a MatrixState) -> Self {
        Self {
            state,
            agent: None,
            task: None,
            previous_tasks: Vec::new(),
        }
    }

    pub fn agent(mut self, agent: &'a AgentCell) -> Self {
        self.agent = Some(agent);
        self
    }

    pub fn task(mut self, task: &'a BoardTask) -> Self {
        self.task = Some(task);
        self
    }

    pub fn previous_tasks(mut self, tasks: Vec<&'a BoardTask>) -> Self {
        self.previous_tasks = tasks;
        self
    }

    pub fn build(self) -> Value {
        let agent_obj = match self.agent {
            Some(a) => json!({
                "id": a.id,
                "name": a.name,
                "slug": a.slug,
                "type": a.agent_type,
                "group": a.group,
                "directory": a.directory,
                "worktree_path": a.worktree_path,
                "worktree_branch": a.worktree_branch,
            }),
            None => stub_context()["agent"].clone(),
        };

        let is_clean = self.agent.map(|a| a.tasks_dispatched == 0).unwrap_or(true);
        let tasks_dispatched = self.agent.map(|a| a.tasks_dispatched).unwrap_or(0);
        let previous_tasks = if self.previous_tasks.is_empty() {
            match (self.agent, self.task) {
                (Some(agent), Some(current_task)) => {
                    let mut linked: Vec<&BoardTask> = self
                        .state
                        .board_tasks
                        .values()
                        .filter(|task| task.agent_id == agent.id && task.id != current_task.id)
                        .collect();
                    linked.sort_by(|a, b| a.created_at.cmp(&b.created_at));
                    linked
                }
                _ => Vec::new(),
            }
        } else {
            self.previous_tasks
        };

        let context_obj = json!({
            "is_clean": is_clean,
            "tasks_dispatched": tasks_dispatched,
            "previous_tasks": previous_tasks.iter().map(|t| {
                json!({
                    "title": t.task,
                    "lane": t.lane,
                    "action": t.action_name,
                })
            }).collect::<Vec<_>>(),
        });

        let worktree_obj = match self.agent {
            Some(a) => json!({
                "active": !a.worktree_path.is_empty(),
                "path": a.worktree_path,
                "branch": a.worktree_branch,
                "base_branch": a.worktree_base_branch,
                "repo_root": a.worktree_repo_root,
                "dirty": a.worktree_dirty,
                "diff": a.worktree_diff,
                "checkpoints": a.worktree_checkpoints,
            }),
            None => stub_context()["worktree"].clone(),
        };

        let task_obj = match self.task {
            Some(t) => {
                let (parent_agent_id, parent_agent_name, parent_agent_slug) =
                    parent_agent_ctx(self.state, t);
                let attachments = normalize_attachments(&t.attachments);
                let artifacts = normalize_artifacts(&t.artifacts);
                json!({
                    "id": t.id,
                    "slug": t.slug,
                    "title": t.task,
                    "description": t.description,
                    "depth": t.pipeline_depth,
                    "is_derived": !t.parent_task_id.is_empty(),
                    "parent_task_id": t.parent_task_id,
                    "parent_agent_id": parent_agent_id,
                    "parent_agent_name": parent_agent_name,
                    "parent_agent_slug": parent_agent_slug,
                    "labels": t.labels,
                    "group": t.group,
                    "status": t.status,
                    "verification_mode": t.verification_mode,
                    "verification_state": t.verification_state,
                    "verification_notes": t.verification_notes,
                    "verification_updated_at": t.verification_updated_at,
                    "verification_updated_by": t.verification_updated_by,
                    "verification_summary": t.verification_summary,
                    "worktree_boundary": t.worktree_boundary,
                    "resume_after_boundary_task_id": t.resume_after_boundary_task_id,
                    "attachments": attachments.iter().map(|a| {
                        json!({
                            "path": a.path,
                            "filename": a.filename,
                        })
                    }).collect::<Vec<_>>(),
                    "artifacts": artifacts,
                    "upstream_artifacts": Vec::<Value>::new(),
                })
            }
            None => stub_context()["task"].clone(),
        };

        let terminals: Vec<Value> = self
            .agent
            .and_then(|a| self.state.children.get(&a.id))
            .cloned()
            .unwrap_or_default()
            .iter()
            .filter_map(|cid| self.state.agents.get(cid))
            .map(|t| {
                json!({
                    "name": t.name,
                    "slug": t.slug,
                    "current_path": t.current_path,
                    "current_process": t.current_process,
                    "current_branch": t.current_branch,
                })
            })
            .collect();

        json!({
            "agent": agent_obj,
            "context": context_obj,
            "worktree": worktree_obj,
            "task": task_obj,
            "terminals": terminals,
        })
    }
}

fn parent_agent_ctx(state: &MatrixState, task: &BoardTask) -> (String, String, String) {
    if task.parent_task_id.is_empty() {
        return (String::new(), String::new(), String::new());
    }
    let Some(parent_task) = state.board_tasks.get(&task.parent_task_id) else {
        return (String::new(), String::new(), String::new());
    };
    if parent_task.agent_id.is_empty() {
        return (String::new(), String::new(), String::new());
    }
    let Some(parent_agent) = state.agents.get(&parent_task.agent_id) else {
        return (String::new(), String::new(), String::new());
    };
    (
        parent_agent.id.clone(),
        parent_agent.name.clone(),
        if parent_agent.slug.is_empty() {
            parent_agent.name.clone()
        } else {
            parent_agent.slug.clone()
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stub_has_expected_shape() {
        let s = stub_context();
        assert!(s["agent"]["name"].is_string());
        assert!(s["context"]["is_clean"].as_bool().unwrap());
    }

    #[test]
    fn builder_reports_clean_for_fresh_agent() {
        let mut state = MatrixState::new();
        state.add_group("Eng").unwrap();
        let a = AgentCell::new("a1", "Worker", "Eng");
        state.add_agent(a).unwrap();
        let agent = state.agents.get("a1").unwrap();
        let ctx = LoomContextBuilder::new(&state).agent(agent).build();
        assert!(ctx["context"]["is_clean"].as_bool().unwrap());
        assert_eq!(ctx["context"]["tasks_dispatched"], 0);
    }
}
