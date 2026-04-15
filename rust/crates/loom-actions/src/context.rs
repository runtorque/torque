//! Build the `loom.*` context namespace for action rendering.
//!
//! Mirrors `_build_loom_context` in `loom/server.py`. Provides safe defaults
//! for preview renders (`LOOM_CONTEXT_STUB`).

use serde_json::{json, Value};

use loom_core::state::{AgentCell, BoardTask, MatrixState};

/// Stub context for preview/discovery renders. Matches Python's
/// `LOOM_CONTEXT_STUB`.
pub fn stub_context() -> Value {
    json!({
        "agent": {
            "id": "preview",
            "name": "Preview",
            "slug": "preview",
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
            "path": "",
            "branch": "",
            "base_branch": "",
            "repo_root": "",
        },
        "task": {
            "id": "",
            "slug": "",
            "title": "",
            "labels": [],
            "group": "",
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
                "group": a.group,
                "directory": a.directory,
                "worktree_path": a.worktree_path,
                "worktree_branch": a.worktree_branch,
            }),
            None => stub_context()["agent"].clone(),
        };

        let is_clean = self.agent.map(|a| a.tasks_dispatched == 0).unwrap_or(true);
        let tasks_dispatched = self.agent.map(|a| a.tasks_dispatched).unwrap_or(0);

        let context_obj = json!({
            "is_clean": is_clean,
            "tasks_dispatched": tasks_dispatched,
            "previous_tasks": self.previous_tasks.iter().map(|t| {
                json!({
                    "id": t.id,
                    "title": t.task,
                    "lane": t.lane,
                    "status": t.status,
                })
            }).collect::<Vec<_>>(),
        });

        let worktree_obj = match self.agent {
            Some(a) => json!({
                "path": a.worktree_path,
                "branch": a.worktree_branch,
                "base_branch": a.worktree_base_branch,
                "repo_root": a.worktree_repo_root,
            }),
            None => stub_context()["worktree"].clone(),
        };

        let task_obj = match self.task {
            Some(t) => json!({
                "id": t.id,
                "slug": t.slug,
                "title": t.task,
                "labels": t.labels,
                "group": t.group,
            }),
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
                    "id": t.id,
                    "name": t.name,
                    "slug": t.slug,
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
