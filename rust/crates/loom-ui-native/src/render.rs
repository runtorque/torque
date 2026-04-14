//! Pure-Rust render logic for the native UI's sidebar and content areas.
//!
//! Kept separate from AppKit so it's trivially unit-tested and platform-free.
//! The AppKit layer calls these to produce display strings, then pushes them
//! into NSTextView / NSTextField / NSOutlineView data sources.

use crate::bridge::MatrixStateSnapshot;

pub fn render_sidebar(snapshot: &MatrixStateSnapshot) -> String {
    use std::fmt::Write;
    let mut out = String::new();
    if snapshot.groups_order.is_empty() {
        out.push_str(
            "Loom\n\nNo groups yet.\n\n\
             Use the CLI or a local HTTP request to create one:\n\
             curl -X POST http://127.0.0.1:18932/api/cmd \\\n  \
             -H 'content-type: application/json' \\\n  \
             -d '{\"cmd\":\"add_group\",\"name\":\"Default\"}'",
        );
        return out;
    }
    let selected = snapshot.selected_agent_id.as_deref();
    writeln!(out, "Loom — {} group(s)", snapshot.groups_order.len()).ok();
    if selected.is_some() {
        writeln!(out, "(▶ = selected)").ok();
    } else {
        writeln!(out, "(select an agent to attach its terminal →)").ok();
    }
    for group in &snapshot.groups_order {
        writeln!(out).ok();
        writeln!(out, "▸ {}", group).ok();
        if let Some(members) = snapshot.groups.get(group) {
            // Only show top-level members (parent_id empty); children are
            // nested under their parent below.
            for agent_id in members {
                let Some(agent) = snapshot.agents.iter().find(|a| &a.id == agent_id) else {
                    continue;
                };
                if !agent.parent_id.is_empty() {
                    continue;
                }
                write_agent_row(&mut out, agent, snapshot, selected, 1);
            }
        }
    }
    out
}

fn write_agent_row(
    out: &mut String,
    agent: &loom_core::state::AgentCell,
    snapshot: &MatrixStateSnapshot,
    selected: Option<&str>,
    depth: usize,
) {
    use std::fmt::Write;
    let indent = "  ".repeat(depth);
    let status_dot = match agent.status.as_str() {
        "running" => "●",
        "error" => "✗",
        _ => "○",
    };
    let marker = if selected == Some(agent.id.as_str()) {
        "▶"
    } else {
        " "
    };
    let type_tag = if agent.cell_type == "terminal" { " ⎔" } else { "" };
    writeln!(out, "{indent}{marker} {status_dot} {}{type_tag}", agent.name).ok();

    // Recurse into children (other agents whose parent_id == this one's id).
    let mut children: Vec<&loom_core::state::AgentCell> = snapshot
        .agents
        .iter()
        .filter(|a| a.parent_id == agent.id)
        .collect();
    // Stable ordering — by name — so the sidebar doesn't jitter between ticks.
    children.sort_by(|a, b| a.name.cmp(&b.name));
    for child in children {
        write_agent_row(out, child, snapshot, selected, depth + 1);
    }
}

pub fn render_content(snapshot: &MatrixStateSnapshot) -> String {
    use std::fmt::Write;
    let mut out = String::new();
    writeln!(
        out,
        "{} group(s), {} agent(s), {} task(s)",
        snapshot.groups_order.len(),
        snapshot.agents.len(),
        snapshot.board_tasks.len()
    )
    .ok();
    writeln!(out).ok();

    let running: Vec<&str> = snapshot
        .agents
        .iter()
        .filter(|a| a.status == "running")
        .map(|a| a.name.as_str())
        .collect();
    if !running.is_empty() {
        writeln!(out, "Running: {}", running.join(", ")).ok();
    }

    let attention: Vec<&str> = snapshot
        .agents
        .iter()
        .filter(|a| a.needs_attention)
        .map(|a| a.name.as_str())
        .collect();
    if !attention.is_empty() {
        writeln!(out, "Needs attention: {}", attention.join(", ")).ok();
    }

    let open_tasks: Vec<&loom_core::state::BoardTask> = snapshot
        .board_tasks
        .iter()
        .filter(|t| t.lane != "Done" && t.lane != "Archived")
        .collect();
    writeln!(out, "\nOpen tasks ({}):", open_tasks.len()).ok();
    for t in open_tasks.iter().take(20) {
        writeln!(out, "  [{}] {} — {}", t.lane, t.id, t.task).ok();
    }

    out
}

pub fn initial_content_placeholder() -> &'static str {
    "Loom engine running.\n\n\
     Terminal rendering lands in Phase 8 via libghostty.\n\
     For now, the engine listens on http://127.0.0.1:18932 — use `bin/loom`\n\
     or the HTTP API to drive it."
}

#[cfg(test)]
mod tests {
    use super::*;
    use loom_core::state::{AgentCell, BoardTask, MatrixState};

    fn empty_snapshot() -> MatrixStateSnapshot {
        MatrixStateSnapshot {
            agents: vec![],
            groups_order: vec![],
            group_slugs: Default::default(),
            groups: Default::default(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
        }
    }

    #[test]
    fn sidebar_empty_shows_hint() {
        let text = render_sidebar(&empty_snapshot());
        assert!(text.contains("No groups"));
        assert!(text.contains("add_group"));
    }

    #[test]
    fn sidebar_renders_group_with_agent_status() {
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        st.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();

        let snap = MatrixStateSnapshot {
            agents: vec![st.agents["a1"].clone()],
            groups_order: vec!["Eng".into()],
            group_slugs: [("Eng".to_string(), "eng".to_string())].into_iter().collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])].into_iter().collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
        };
        let text = render_sidebar(&snap);
        assert!(text.contains("▸ Eng"));
        assert!(text.contains("Worker"));
        assert!(text.contains("○"), "expected idle dot, got: {text}");
    }

    #[test]
    fn content_counts_summary() {
        let text = render_content(&empty_snapshot());
        assert!(text.contains("0 group"));
        assert!(text.contains("0 agent"));
        assert!(text.contains("0 task"));
    }

    #[test]
    fn content_lists_open_tasks_under_20() {
        let mut tasks = Vec::new();
        for i in 0..25 {
            let mut t = BoardTask::new_minimal(format!("t-{i}"), format!("task {i}"));
            t.group = "Eng".into();
            t.lane = "To Do".into();
            tasks.push(t);
        }
        let snap = MatrixStateSnapshot {
            agents: vec![],
            groups_order: vec!["Eng".into()],
            group_slugs: Default::default(),
            groups: Default::default(),
            board_lanes: vec![],
            board_tasks: tasks,
            global_default_command: String::new(),
            selected_agent_id: None,
        };
        let text = render_content(&snap);
        assert!(text.contains("Open tasks (25)"));
        assert!(text.contains("task 0"));
        assert!(text.contains("task 19"));
        assert!(!text.contains("task 22"));
    }

    #[test]
    fn sidebar_marks_selected_agent() {
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        st.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();

        let agents = vec![st.agents["a1"].clone()];
        let snap = MatrixStateSnapshot {
            agents,
            groups_order: vec!["Eng".into()],
            group_slugs: [("Eng".to_string(), "eng".to_string())].into_iter().collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])].into_iter().collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: Some("a1".into()),
        };
        let text = render_sidebar(&snap);
        assert!(text.contains("▶"), "expected selected marker, got: {text}");
        assert!(text.contains("selected"));
    }

    #[test]
    fn sidebar_nests_child_terminals_under_parent() {
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        st.add_agent(AgentCell::new("p1", "Parent", "Eng")).unwrap();
        let mut child = AgentCell::new("c1", "Logs", "Eng");
        child.parent_id = "p1".into();
        child.cell_type = "terminal".into();
        st.add_agent(child).unwrap();

        let agents: Vec<_> = st.agents.values().cloned().collect();
        let groups = std::collections::HashMap::from([(
            "Eng".to_string(),
            vec!["p1".to_string(), "c1".to_string()],
        )]);
        let snap = MatrixStateSnapshot {
            agents,
            groups_order: vec!["Eng".into()],
            group_slugs: [("Eng".to_string(), "eng".to_string())].into_iter().collect(),
            groups,
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
        };
        let text = render_sidebar(&snap);
        let parent_line = text.lines().find(|l| l.contains("Parent")).unwrap();
        let child_line = text.lines().find(|l| l.contains("Logs")).unwrap();
        let leading = |s: &str| s.chars().take_while(|c| *c == ' ').count();
        assert!(
            leading(child_line) > leading(parent_line),
            "child must be indented deeper than parent.\n  parent: {parent_line:?}\n  child:  {child_line:?}"
        );
        assert!(child_line.contains("⎔"), "terminal cell type tag missing");
    }

    #[test]
    fn content_filters_done_and_archived() {
        let mut done = BoardTask::new_minimal("t-1", "done task");
        done.lane = "Done".into();
        let mut archived = BoardTask::new_minimal("t-2", "archived task");
        archived.lane = "Archived".into();
        let mut open = BoardTask::new_minimal("t-3", "open task");
        open.lane = "To Do".into();

        let snap = MatrixStateSnapshot {
            agents: vec![],
            groups_order: vec![],
            group_slugs: Default::default(),
            groups: Default::default(),
            board_lanes: vec![],
            board_tasks: vec![done, archived, open],
            global_default_command: String::new(),
            selected_agent_id: None,
        };
        let text = render_content(&snap);
        assert!(text.contains("open task"));
        assert!(!text.contains("done task"));
        assert!(!text.contains("archived task"));
    }
}
