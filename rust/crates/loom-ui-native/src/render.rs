//! Pure-Rust render logic for the native UI's sidebar and content areas.
//!
//! Kept separate from AppKit so it's trivially unit-tested and platform-free.
//! The AppKit layer calls these to produce display strings, then pushes them
//! into NSTextView / NSTextField / NSOutlineView data sources.

use serde::Serialize;

use crate::bridge::MatrixStateSnapshot;

/// Typed sidebar tree consumed by the AppKit NSOutlineView data source.
///
/// Built from a `MatrixStateSnapshot` by [`build_sidebar_tree`]. Every node
/// carries a stable opaque id — groups by name, agents/terminals by cell id —
/// so the outline view can compute a JSON signature to skip rebuilds when the
/// snapshot hasn't meaningfully changed.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SidebarTree {
    pub groups: Vec<SidebarGroup>,
    pub selected_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SidebarGroup {
    pub name: String,
    pub slug: String,
    /// Top-level cells (agents + orphan terminals). Child terminals live
    /// nested under their parent agent in `SidebarNode::children`.
    pub nodes: Vec<SidebarNode>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SidebarNode {
    pub id: String,
    pub name: String,
    pub cell_type: String,
    pub status: String,
    pub type_label: Option<String>,
    pub subtitle: Option<String>,
    pub needs_attention: bool,
    pub children: Vec<SidebarNode>,
}

pub fn build_sidebar_tree(snapshot: &MatrixStateSnapshot) -> SidebarTree {
    let mut groups = Vec::with_capacity(snapshot.groups_order.len());
    for group_name in &snapshot.groups_order {
        let slug = snapshot
            .group_slugs
            .get(group_name)
            .cloned()
            .unwrap_or_default();
        let members = snapshot.groups.get(group_name);
        let mut nodes = Vec::new();
        if let Some(ids) = members {
            for id in ids {
                let Some(agent) = snapshot.find_agent(id) else {
                    continue;
                };
                // Top-level members only — children get attached recursively
                // below. Skip cells whose parent is another cell in the same
                // group.
                if !agent.parent_id.is_empty() {
                    continue;
                }
                nodes.push(build_node(agent, snapshot));
            }
        }
        groups.push(SidebarGroup {
            name: group_name.clone(),
            slug,
            nodes,
        });
    }
    SidebarTree {
        groups,
        selected_id: snapshot.selected_agent_id.clone(),
    }
}

fn build_node(agent: &loom_core::state::AgentCell, snapshot: &MatrixStateSnapshot) -> SidebarNode {
    // Stable ordering for children: name asc.
    let mut children: Vec<&loom_core::state::AgentCell> = snapshot
        .agents
        .iter()
        .filter(|a| a.parent_id == agent.id)
        .collect();
    children.sort_by(|a, b| a.name.cmp(&b.name));
    SidebarNode {
        id: agent.id.clone(),
        name: agent.name.clone(),
        cell_type: agent.cell_type.clone(),
        status: agent.status.clone(),
        type_label: derive_type_label(agent),
        subtitle: derive_subtitle(agent),
        needs_attention: agent.needs_attention,
        children: children
            .into_iter()
            .map(|c| build_node(c, snapshot))
            .collect(),
    }
}

fn derive_type_label(agent: &loom_core::state::AgentCell) -> Option<String> {
    if agent.cell_type == "terminal" {
        let proc = agent.current_process.trim();
        if !proc.is_empty() {
            return Some(proc.to_string());
        }
        return Some("term".to_string());
    }
    // Agent type: honor the adapter-detected label first, fall back to
    // parsing the command (e.g. "claude" → "Claude Code", "codex" → "Codex").
    let explicit = agent.agent_type.trim();
    if !explicit.is_empty() {
        return Some(explicit.to_string());
    }
    let cmd = agent.command.trim();
    if cmd.is_empty() {
        return None;
    }
    let first = cmd.split_whitespace().next().unwrap_or("");
    let base = first.rsplit('/').next().unwrap_or(first);
    match base {
        "claude" => Some("Claude Code".into()),
        "codex" => Some("Codex".into()),
        "gemini" => Some("Gemini".into()),
        _ if base.is_empty() => None,
        _ => Some(base.to_string()),
    }
}

fn derive_subtitle(agent: &loom_core::state::AgentCell) -> Option<String> {
    if agent.cell_type == "terminal" {
        let path = agent.current_path.trim();
        if path.is_empty() {
            return None;
        }
        return Some(shorten_path(path));
    }
    let branch = pick_nonempty(&agent.worktree_branch, &agent.current_branch);
    let dir = pick_nonempty(&agent.directory, &agent.current_path);
    match (branch, dir) {
        (Some(b), Some(d)) => Some(format!("{b} · {}", shorten_path(d))),
        (Some(b), None) => Some(b.to_string()),
        (None, Some(d)) => Some(shorten_path(d)),
        (None, None) => None,
    }
}

fn pick_nonempty<'a>(a: &'a str, b: &'a str) -> Option<&'a str> {
    let a = a.trim();
    if !a.is_empty() {
        return Some(a);
    }
    let b = b.trim();
    if !b.is_empty() {
        Some(b)
    } else {
        None
    }
}

fn shorten_path(p: &str) -> String {
    // Show only the basename for brevity. If the path is just a basename,
    // this is idempotent.
    match p.rsplit('/').next() {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => p.to_string(),
    }
}

// ---------------------------------------------------------------------------
// Board tree
// ---------------------------------------------------------------------------

/// Typed board view consumed by the AppKit Board panel's NSTableView data
/// source. Produced from a [`MatrixStateSnapshot`] + the currently-active
/// lane + group filter.
///
/// Cards are flattened into a single ordered list of [`BoardRow`] so the
/// table renders them linearly, with `depth` driving indent for derived
/// tasks. Parents appear before their children (pre-order walk).
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BoardView {
    /// All lanes in engine order.
    pub lanes: Vec<String>,
    /// The lane being displayed (a tab choice by the UI).
    pub active_lane: String,
    /// Optional group filter. `None` = all groups.
    pub group_filter: Option<String>,
    /// Flat pre-order card list. `depth=0` for top-level, `depth>0` for
    /// derived tasks nested under their parent.
    pub rows: Vec<BoardRow>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BoardRow {
    pub id: String,
    pub title: String,
    pub lane: String,
    pub group: String,
    pub labels: Vec<String>,
    pub action_name: String,
    pub agent_id: String,
    pub assignee: String,
    pub status: String,
    pub depth: u32,
    /// True if this card has derived children in the active lane (so the
    /// UI can render a disclosure arrow, if desired).
    pub has_children: bool,
    /// True when `lane == "Done"` — used to dim/strike-through in the UI.
    pub is_done: bool,
    /// True when the card is in the active lane. Children in other lanes
    /// still show under their parent but greyed out.
    pub in_active_lane: bool,
}

pub fn build_board_tree(
    snapshot: &MatrixStateSnapshot,
    active_lane: &str,
    group_filter: Option<&str>,
) -> BoardView {
    // Filter pool: tasks matching group (if any), excluding archived.
    let mut pool: Vec<&loom_core::state::BoardTask> = snapshot
        .board_tasks
        .iter()
        .filter(|t| t.lane != "Archived")
        .filter(|t| match group_filter {
            Some(g) => t.group == g,
            None => true,
        })
        .collect();
    pool.sort_by_key(|t| t.position);

    // Build parent → children index (over the filtered pool) so we can
    // walk in order.
    let mut children_by_parent: std::collections::HashMap<&str, Vec<&loom_core::state::BoardTask>> =
        std::collections::HashMap::new();
    for t in &pool {
        children_by_parent
            .entry(t.parent_task_id.as_str())
            .or_default()
            .push(t);
    }
    for v in children_by_parent.values_mut() {
        v.sort_by_key(|t| t.position);
    }

    // Roots for the active lane: top-level tasks in that lane, plus any
    // tasks whose parent is NOT in pool (orphans) so we don't drop them.
    let mut rows: Vec<BoardRow> = Vec::new();
    let pool_ids: std::collections::HashSet<&str> = pool.iter().map(|t| t.id.as_str()).collect();

    for t in pool.iter().filter(|t| t.lane == active_lane) {
        let parent_in_pool =
            !t.parent_task_id.is_empty() && pool_ids.contains(t.parent_task_id.as_str());
        if parent_in_pool {
            continue;
        }
        walk_board_row(t, &children_by_parent, 0, active_lane, &mut rows);
    }

    BoardView {
        lanes: snapshot.board_lanes.clone(),
        active_lane: active_lane.to_string(),
        group_filter: group_filter.map(|s| s.to_string()),
        rows,
    }
}

/// All-lanes-visible kanban view: one column per lane, excluding Archived
/// (which becomes a toggle-revealed archive drawer in a future iteration).
///
/// Internally reuses [`build_board_tree`] per lane so the nested-derived
/// walking and filter rules stay consistent across the two entry points.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BoardColumns {
    pub columns: Vec<BoardColumn>,
    pub group_filter: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BoardColumn {
    pub lane: String,
    pub rows: Vec<BoardRow>,
}

pub fn build_board_columns(
    snapshot: &MatrixStateSnapshot,
    group_filter: Option<&str>,
) -> BoardColumns {
    let lanes: Vec<String> = snapshot
        .board_lanes
        .iter()
        .filter(|l| l.as_str() != "Archived")
        .cloned()
        .collect();
    let columns = lanes
        .into_iter()
        .map(|lane| {
            let view = build_board_tree(snapshot, &lane, group_filter);
            BoardColumn {
                lane,
                rows: view.rows,
            }
        })
        .collect();
    BoardColumns {
        columns,
        group_filter: group_filter.map(|s| s.to_string()),
    }
}

fn walk_board_row<'a>(
    task: &'a loom_core::state::BoardTask,
    children_by_parent: &std::collections::HashMap<&'a str, Vec<&'a loom_core::state::BoardTask>>,
    depth: u32,
    active_lane: &str,
    out: &mut Vec<BoardRow>,
) {
    let kids = children_by_parent
        .get(task.id.as_str())
        .map(|v| v.as_slice())
        .unwrap_or(&[]);
    out.push(BoardRow {
        id: task.id.clone(),
        title: task.task.clone(),
        lane: task.lane.clone(),
        group: task.group.clone(),
        labels: task.labels.clone(),
        action_name: task.action_name.clone(),
        agent_id: task.agent_id.clone(),
        assignee: String::new(), // reserved for future (assignee field not in state)
        status: task.status.clone(),
        depth,
        has_children: !kids.is_empty(),
        is_done: task.lane == "Done",
        in_active_lane: task.lane == active_lane,
    });
    for child in kids {
        walk_board_row(child, children_by_parent, depth + 1, active_lane, out);
    }
}

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
    let type_tag = if agent.cell_type == "terminal" {
        " ⎔"
    } else {
        ""
    };
    writeln!(
        out,
        "{indent}{marker} {status_dot} {}{type_tag}",
        agent.name
    )
    .ok();

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
            content_layout: Default::default(),
            dock_layout: Default::default(),
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
            group_slugs: [("Eng".to_string(), "eng".to_string())]
                .into_iter()
                .collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])]
                .into_iter()
                .collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
            content_layout: Default::default(),
            dock_layout: Default::default(),
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
            content_layout: Default::default(),
            dock_layout: Default::default(),
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
            group_slugs: [("Eng".to_string(), "eng".to_string())]
                .into_iter()
                .collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])]
                .into_iter()
                .collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: Some("a1".into()),
            content_layout: Default::default(),
            dock_layout: Default::default(),
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
            group_slugs: [("Eng".to_string(), "eng".to_string())]
                .into_iter()
                .collect(),
            groups,
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
            content_layout: Default::default(),
            dock_layout: Default::default(),
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
    fn tree_groups_top_level_agents_skip_children() {
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        st.add_agent(AgentCell::new("a1", "Parent", "Eng")).unwrap();
        let mut child = AgentCell::new("t1", "Logs", "Eng");
        child.parent_id = "a1".into();
        child.cell_type = "terminal".into();
        child.current_process = "vim".into();
        child.current_path = "/home/me/proj".into();
        st.add_agent(child).unwrap();

        let agents: Vec<_> = st.agents.values().cloned().collect();
        let groups = std::collections::HashMap::from([(
            "Eng".to_string(),
            vec!["a1".to_string(), "t1".to_string()],
        )]);
        let group_slugs = std::collections::HashMap::from([("Eng".to_string(), "eng".to_string())]);
        let snap = MatrixStateSnapshot {
            agents,
            groups_order: vec!["Eng".into()],
            group_slugs,
            groups,
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: Some("a1".into()),
            content_layout: Default::default(),
            dock_layout: Default::default(),
        };

        let tree = build_sidebar_tree(&snap);
        assert_eq!(tree.selected_id.as_deref(), Some("a1"));
        assert_eq!(tree.groups.len(), 1);
        let g = &tree.groups[0];
        assert_eq!(g.name, "Eng");
        assert_eq!(g.slug, "eng");
        // One top-level node (Parent); child Logs is nested.
        assert_eq!(g.nodes.len(), 1);
        let parent = &g.nodes[0];
        assert_eq!(parent.id, "a1");
        assert_eq!(parent.children.len(), 1);
        let child = &parent.children[0];
        assert_eq!(child.id, "t1");
        assert_eq!(child.name, "Logs");
        assert_eq!(child.cell_type, "terminal");
        // Terminal subtitle + type_label come from current_process/path.
        assert_eq!(child.type_label.as_deref(), Some("vim"));
        assert_eq!(child.subtitle.as_deref(), Some("proj"));
    }

    #[test]
    fn tree_agent_subtitle_combines_branch_and_dir() {
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        let mut a = AgentCell::new("a1", "Worker", "Eng");
        a.worktree_branch = "feature/x".into();
        a.directory = "/Users/me/dev/project".into();
        a.command = "claude".into();
        st.add_agent(a).unwrap();

        let snap = MatrixStateSnapshot {
            agents: vec![st.agents["a1"].clone()],
            groups_order: vec!["Eng".into()],
            group_slugs: [("Eng".to_string(), "eng".to_string())]
                .into_iter()
                .collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])]
                .into_iter()
                .collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
            content_layout: Default::default(),
            dock_layout: Default::default(),
        };

        let tree = build_sidebar_tree(&snap);
        let node = &tree.groups[0].nodes[0];
        assert_eq!(node.type_label.as_deref(), Some("Claude Code"));
        assert_eq!(node.subtitle.as_deref(), Some("feature/x · project"));
    }

    #[test]
    fn tree_signature_stable_across_rebuilds() {
        // Same snapshot → identical trees → identical JSON signatures. Used
        // by the AppKit layer to skip outline rebuilds.
        let mut st = MatrixState::new();
        st.add_group("Eng").unwrap();
        st.add_agent(AgentCell::new("a1", "Worker", "Eng")).unwrap();

        let snap = MatrixStateSnapshot {
            agents: vec![st.agents["a1"].clone()],
            groups_order: vec!["Eng".into()],
            group_slugs: [("Eng".to_string(), "eng".to_string())]
                .into_iter()
                .collect(),
            groups: [("Eng".to_string(), vec!["a1".to_string()])]
                .into_iter()
                .collect(),
            board_lanes: vec![],
            board_tasks: vec![],
            global_default_command: String::new(),
            selected_agent_id: None,
            content_layout: Default::default(),
            dock_layout: Default::default(),
        };
        let a = serde_json::to_string(&build_sidebar_tree(&snap)).unwrap();
        let b = serde_json::to_string(&build_sidebar_tree(&snap)).unwrap();
        assert_eq!(a, b);
    }

    fn task(id: &str, lane: &str, group: &str, pos: i64) -> BoardTask {
        let mut t = BoardTask::new_minimal(id, format!("task {id}"));
        t.lane = lane.into();
        t.group = group.into();
        t.position = pos;
        t
    }

    fn board_snap_with_tasks(tasks: Vec<BoardTask>) -> MatrixStateSnapshot {
        MatrixStateSnapshot {
            agents: vec![],
            groups_order: vec![],
            group_slugs: Default::default(),
            groups: Default::default(),
            board_lanes: vec![
                "Backlog".into(),
                "To Do".into(),
                "In Progress".into(),
                "Done".into(),
            ],
            board_tasks: tasks,
            global_default_command: String::new(),
            selected_agent_id: None,
            content_layout: Default::default(),
            dock_layout: Default::default(),
        }
    }

    #[test]
    fn board_tree_filters_by_active_lane() {
        let snap = board_snap_with_tasks(vec![
            task("a", "Backlog", "Eng", 0),
            task("b", "To Do", "Eng", 0),
            task("c", "Done", "Eng", 0),
        ]);
        let view = build_board_tree(&snap, "To Do", None);
        assert_eq!(view.active_lane, "To Do");
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].id, "b");
    }

    #[test]
    fn board_tree_filters_out_archived() {
        let mut arch = task("x", "Archived", "Eng", 0);
        arch.lane = "Archived".into();
        let snap = board_snap_with_tasks(vec![arch, task("a", "Backlog", "Eng", 0)]);
        let view = build_board_tree(&snap, "Backlog", None);
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].id, "a");
    }

    #[test]
    fn board_tree_group_filter() {
        let snap = board_snap_with_tasks(vec![
            task("a", "Backlog", "Eng", 0),
            task("b", "Backlog", "Platform", 1),
        ]);
        let view = build_board_tree(&snap, "Backlog", Some("Eng"));
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].id, "a");
    }

    #[test]
    fn board_tree_nests_derived_tasks_pre_order() {
        let mut parent = task("p", "In Progress", "Eng", 0);
        let mut child = task("c", "In Progress", "Eng", 0);
        child.parent_task_id = "p".into();
        let mut grandchild = task("gc", "In Progress", "Eng", 0);
        grandchild.parent_task_id = "c".into();
        let mut other_top = task("o", "In Progress", "Eng", 5);
        other_top.position = 5; // after parent
        let snap = board_snap_with_tasks(vec![
            grandchild.clone(),
            child.clone(),
            parent.clone(),
            other_top.clone(),
        ]);
        let view = build_board_tree(&snap, "In Progress", None);
        // Expected: parent (0), child (1), grandchild (2), other_top (0)
        let ids: Vec<&str> = view.rows.iter().map(|r| r.id.as_str()).collect();
        assert_eq!(ids, vec!["p", "c", "gc", "o"]);
        assert_eq!(view.rows[0].depth, 0);
        assert_eq!(view.rows[1].depth, 1);
        assert_eq!(view.rows[2].depth, 2);
        assert_eq!(view.rows[3].depth, 0);
        assert!(view.rows[0].has_children);
        assert!(view.rows[1].has_children);
        assert!(!view.rows[2].has_children);
    }

    #[test]
    fn board_columns_one_per_non_archived_lane_in_engine_order() {
        let snap = board_snap_with_tasks(vec![
            task("a", "Backlog", "Eng", 0),
            task("b", "To Do", "Eng", 0),
            task("c", "In Progress", "Eng", 0),
            task("d", "Done", "Eng", 0),
        ]);
        let cols = build_board_columns(&snap, None);
        let lanes: Vec<&str> = cols.columns.iter().map(|c| c.lane.as_str()).collect();
        assert_eq!(lanes, vec!["Backlog", "To Do", "In Progress", "Done"]);
        assert_eq!(cols.columns[0].rows.len(), 1);
        assert_eq!(cols.columns[0].rows[0].id, "a");
        assert_eq!(cols.columns[1].rows[0].id, "b");
        assert_eq!(cols.columns[2].rows[0].id, "c");
        assert_eq!(cols.columns[3].rows[0].id, "d");
    }

    #[test]
    fn board_columns_excludes_archived_lane_and_tasks() {
        let mut tasks = vec![
            task("a", "Backlog", "Eng", 0),
            task("b", "Archived", "Eng", 0),
        ];
        tasks[1].lane = "Archived".into();
        let mut snap = board_snap_with_tasks(tasks);
        snap.board_lanes.push("Archived".into());
        let cols = build_board_columns(&snap, None);
        // No "Archived" column.
        assert!(cols.columns.iter().all(|c| c.lane != "Archived"));
        // Archived task doesn't appear in Backlog either.
        assert!(cols
            .columns
            .iter()
            .find(|c| c.lane == "Backlog")
            .unwrap()
            .rows
            .iter()
            .all(|r| r.id != "b"));
    }

    #[test]
    fn board_columns_propagate_group_filter() {
        let snap = board_snap_with_tasks(vec![
            task("a", "Backlog", "Eng", 0),
            task("b", "Backlog", "Platform", 1),
        ]);
        let cols = build_board_columns(&snap, Some("Platform"));
        assert_eq!(cols.group_filter.as_deref(), Some("Platform"));
        let backlog = cols.columns.iter().find(|c| c.lane == "Backlog").unwrap();
        assert_eq!(backlog.rows.len(), 1);
        assert_eq!(backlog.rows[0].id, "b");
    }

    #[test]
    fn board_tree_child_in_other_lane_renders_under_parent_dimmed() {
        // Parity with Python's board: derived tasks show under their parent
        // even if they've moved on to another lane (typically Done). The
        // UI dims them; the row still carries the real lane + is_done so
        // the UI can style it correctly.
        let parent = task("p", "In Progress", "Eng", 0);
        let mut child = task("c", "Done", "Eng", 0);
        child.parent_task_id = "p".into();
        let snap = board_snap_with_tasks(vec![parent, child]);
        let view = build_board_tree(&snap, "In Progress", None);
        assert_eq!(view.rows.len(), 2);
        assert_eq!(view.rows[0].id, "p");
        assert_eq!(view.rows[0].depth, 0);
        assert!(view.rows[0].in_active_lane);
        assert_eq!(view.rows[1].id, "c");
        assert_eq!(view.rows[1].depth, 1);
        assert!(view.rows[1].is_done);
        assert!(!view.rows[1].in_active_lane);
        assert!(view.rows[0].has_children);
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
            content_layout: Default::default(),
            dock_layout: Default::default(),
        };
        let text = render_content(&snap);
        assert!(text.contains("open task"));
        assert!(!text.contains("done task"));
        assert!(!text.contains("archived task"));
    }
}
