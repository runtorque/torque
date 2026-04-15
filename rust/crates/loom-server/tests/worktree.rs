//! Worktree integration test — sets up a real temp git repo + agent, then
//! exercises create/checkpoint/diff/remove.
//!
//! Skipped automatically if `git` is not on PATH.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::Mutex;

use loom_core::db::LoomDb;
use loom_core::events::EventBus;
use loom_core::state::{AgentCell, MatrixState};

fn git_available() -> bool {
    Command::new("git")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn run_git(dir: &Path, args: &[&str]) {
    let status = Command::new("git").current_dir(dir).args(args).status().unwrap();
    assert!(status.success(), "git {args:?} in {dir:?} failed");
}

fn init_repo(dir: &Path) {
    run_git(dir, &["init", "-q", "-b", "main"]);
    run_git(dir, &["config", "user.email", "loom@test.dev"]);
    run_git(dir, &["config", "user.name", "loom-test"]);
    run_git(dir, &["config", "commit.gpgsign", "false"]);
    std::fs::write(dir.join("README.md"), "hello\n").unwrap();
    run_git(dir, &["add", "."]);
    run_git(dir, &["commit", "-q", "-m", "initial"]);
}

async fn spawn_server_with_agent(
    repo_root: &Path,
) -> (SocketAddr, Arc<Mutex<MatrixState>>, String) {
    let db = LoomDb::in_memory().unwrap();
    let state = Arc::new(Mutex::new(MatrixState::new()));
    let bus = EventBus::new();

    // seed an Eng group + an agent with repo_root set
    let agent_id = {
        let mut st = state.lock().await;
        st.add_group("Eng").unwrap();
        let mut cell = AgentCell::new(uuid::Uuid::new_v4().to_string(), "Worker", "Eng");
        cell.directory = repo_root.to_string_lossy().to_string();
        cell.git_root = repo_root.to_string_lossy().to_string();
        let id = cell.id.clone();
        st.add_agent(cell).unwrap();
        st.drain_deltas(); // clear accumulator
        id
    };

    let app_state = loom_server::app::AppState {
        db,
        state: state.clone(),
        bus,
        pty: None,
        ui_agents: Default::default(),
    };

    let router = loom_server::app::build_router(app_state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });

    (addr, state, agent_id)
}

async fn post(addr: SocketAddr, body: Value) -> Value {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://{}/api/cmd", addr))
        .json(&body)
        .send()
        .await
        .unwrap();
    resp.json::<Value>().await.unwrap()
}

#[tokio::test]
async fn worktree_create_checkpoint_diff_remove() {
    if !git_available() {
        eprintln!("git not available, skipping");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().to_path_buf();
    init_repo(&repo);

    let (addr, state, agent_id) = spawn_server_with_agent(&repo).await;

    // Create the worktree on branch "loom/worker" from main.
    let v = post(
        addr,
        json!({
            "cmd": "worktree_create",
            "agent_id": &agent_id,
            "branch": "loom/worker",
            "base": "main"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "create response: {v:?}");
    let path = PathBuf::from(v["data"]["path"].as_str().unwrap());
    assert!(path.exists(), "worktree path {} does not exist", path.display());

    // Make a change + checkpoint.
    std::fs::write(path.join("new.txt"), "content\n").unwrap();
    let v = post(
        addr,
        json!({
            "cmd": "worktree_checkpoint",
            "agent_id": &agent_id,
            "message": "loom: test"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "checkpoint response: {v:?}");

    // Agent state reflects the checkpoint.
    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(!agent.worktree_path.is_empty());
    assert_eq!(agent.worktree_branch, "loom/worker");
    assert!(agent.worktree_checkpoints >= 1);
    drop(st);

    // Make another change without committing → diff reports dirty.
    std::fs::write(path.join("another.txt"), "more\n").unwrap();
    let v = post(
        addr,
        json!({"cmd": "worktree_diff", "agent_id": &agent_id}),
    )
    .await;
    assert!(v["data"]["files"].as_u64().unwrap_or(0) >= 1, "expected dirty, got {v:?}");

    // Remove the worktree.
    let v = post(
        addr,
        json!({"cmd": "worktree_remove", "agent_id": &agent_id}),
    )
    .await;
    assert_eq!(v["ok"], true, "remove response: {v:?}");
    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(agent.worktree_path.is_empty());
}
