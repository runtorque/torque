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
    let status = Command::new("git")
        .current_dir(dir)
        .args(args)
        .status()
        .unwrap();
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
        terminal_bridge: loom_server::terminal_bridge::TerminalBridgeClient::default(),
        terminals: Default::default(),
        weaver_buffer: loom_server::weaver_buffer::WeaverEventBuffer::default(),
        notifier: loom_server::notifications::NotificationManager::new(),
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
    assert!(
        path.exists(),
        "worktree path {} does not exist",
        path.display()
    );

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
    let v = post(addr, json!({"cmd": "worktree_diff", "agent_id": &agent_id})).await;
    assert!(
        v["data"]["files"].as_u64().unwrap_or(0) >= 1,
        "expected dirty, got {v:?}"
    );

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

/// Regression test for the silent data-loss bug where `worktree_merge`
/// returned `ok: true` for a branch with no commits ahead of base, then
/// happily deleted the branch + worktree. Now it must return `ok: false`
/// and leave the worktree + branch intact so the operator can recover.
#[tokio::test]
async fn worktree_merge_refuses_noop_and_preserves_worktree() {
    if !git_available() {
        eprintln!("git not available, skipping");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().to_path_buf();
    init_repo(&repo);

    let (addr, state, agent_id) = spawn_server_with_agent(&repo).await;

    // Create the worktree on a fresh branch from main. No commits are
    // made on the branch, so it is identical to main.
    let v = post(
        addr,
        json!({
            "cmd": "worktree_create",
            "agent_id": &agent_id,
            "branch": "loom/noop",
            "base": "main"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "create response: {v:?}");
    let worktree_path = PathBuf::from(v["data"]["path"].as_str().unwrap());
    assert!(worktree_path.exists());

    let base_head_before = Command::new("git")
        .current_dir(&repo)
        .args(["rev-parse", "HEAD"])
        .output()
        .unwrap()
        .stdout;

    // Attempt to merge the identical branch back into main.
    let v = post(
        addr,
        json!({
            "cmd": "worktree_merge",
            "agent_id": &agent_id,
        }),
    )
    .await;

    // The inner merge result must NOT report success. (`v["ok"]` is the
    // outer `/api/cmd` dispatch envelope — it stays `true` as long as
    // the command is recognized; the real merge verdict is `v["data"]`.)
    let data = &v["data"];
    assert_eq!(
        data["ok"], false,
        "inner merge result should be ok:false: {v:?}"
    );
    let err = data["error"].as_str().unwrap_or("");
    assert!(
        err.contains("no new commits") || err.contains("up to date") || err.contains("degenerate"),
        "expected no-op error, got: {err}"
    );

    // The worktree directory must still exist.
    assert!(
        worktree_path.exists(),
        "worktree dir was removed on a no-op merge: {}",
        worktree_path.display()
    );

    // The branch must still exist.
    let branch_show = Command::new("git")
        .current_dir(&repo)
        .args(["show-ref", "--verify", "refs/heads/loom/noop"])
        .status()
        .unwrap();
    assert!(
        branch_show.success(),
        "branch was deleted on a no-op merge"
    );

    // Agent cell state must still reflect the worktree.
    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(
        !agent.worktree_path.is_empty(),
        "cell.worktree_path was cleared on a no-op merge"
    );
    assert_eq!(agent.worktree_branch, "loom/noop");
    assert!(
        !agent.worktree_merged,
        "cell.worktree_merged was set on a no-op merge"
    );
    drop(st);

    // Main HEAD must not have advanced.
    let base_head_after = Command::new("git")
        .current_dir(&repo)
        .args(["rev-parse", "HEAD"])
        .output()
        .unwrap()
        .stdout;
    assert_eq!(
        base_head_before, base_head_after,
        "main advanced despite no commits on branch"
    );
}

/// Happy path: a branch with real commits merges, main advances, and
/// the worktree + branch are cleaned up.
#[tokio::test]
async fn worktree_merge_succeeds_on_real_commits() {
    if !git_available() {
        eprintln!("git not available, skipping");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().to_path_buf();
    init_repo(&repo);

    let (addr, state, agent_id) = spawn_server_with_agent(&repo).await;

    let v = post(
        addr,
        json!({
            "cmd": "worktree_create",
            "agent_id": &agent_id,
            "branch": "loom/real",
            "base": "main"
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "create response: {v:?}");
    let worktree_path = PathBuf::from(v["data"]["path"].as_str().unwrap());

    // Make a real commit on the worker branch.
    std::fs::write(worktree_path.join("new.txt"), "content\n").unwrap();
    run_git(&worktree_path, &["add", "."]);
    run_git(&worktree_path, &["commit", "-q", "-m", "feat: add new.txt"]);

    let base_before = String::from_utf8(
        Command::new("git")
            .current_dir(&repo)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap()
            .stdout,
    )
    .unwrap()
    .trim()
    .to_string();

    let v = post(
        addr,
        json!({
            "cmd": "worktree_merge",
            "agent_id": &agent_id,
        }),
    )
    .await;
    assert_eq!(v["ok"], true, "merge response: {v:?}");
    assert_eq!(v["data"]["ok"], true);
    let merge_sha = v["data"]["merge_sha"].as_str().unwrap_or("").to_string();
    assert!(!merge_sha.is_empty(), "merge_sha should be populated");

    let base_after = String::from_utf8(
        Command::new("git")
            .current_dir(&repo)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap()
            .stdout,
    )
    .unwrap()
    .trim()
    .to_string();
    assert_ne!(base_before, base_after, "main did not advance after a real merge");
    assert_eq!(base_after, merge_sha);

    // Cleanup did happen — worktree gone, branch gone, cell cleared.
    assert!(
        !worktree_path.exists(),
        "worktree dir was preserved after a successful merge"
    );
    let branch_show = Command::new("git")
        .current_dir(&repo)
        .args(["show-ref", "--verify", "refs/heads/loom/real"])
        .status()
        .unwrap();
    assert!(
        !branch_show.success(),
        "branch survived a successful merge+cleanup"
    );
    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(agent.worktree_path.is_empty());
    assert!(agent.worktree_branch.is_empty());
    assert!(agent.worktree_merged);
}

/// A dirty worktree without auto-checkpoint must block the merge and
/// leave the branch + worktree intact. Mirrors Python's
/// `_run_worktree_merge_check` dirty-worktree block.
#[tokio::test]
async fn worktree_merge_blocks_on_dirty_without_auto_checkpoint() {
    if !git_available() {
        eprintln!("git not available, skipping");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().to_path_buf();
    init_repo(&repo);

    let (addr, state, agent_id) = spawn_server_with_agent(&repo).await;

    let v = post(
        addr,
        json!({
            "cmd": "worktree_create",
            "agent_id": &agent_id,
            "branch": "loom/dirty",
            "base": "main"
        }),
    )
    .await;
    assert_eq!(v["ok"], true);
    let worktree_path = PathBuf::from(v["data"]["path"].as_str().unwrap());

    // Commit one real change, then leave another uncommitted.
    std::fs::write(worktree_path.join("a.txt"), "one\n").unwrap();
    run_git(&worktree_path, &["add", "."]);
    run_git(&worktree_path, &["commit", "-q", "-m", "a"]);
    std::fs::write(worktree_path.join("b.txt"), "two\n").unwrap();
    // b.txt is uncommitted → worktree is dirty.

    // Ensure auto_checkpoint is OFF.
    {
        let mut st = state.lock().await;
        let cell = st.agents.get_mut(&agent_id).unwrap();
        cell.worktree_auto_checkpoint = false;
    }

    let v = post(
        addr,
        json!({
            "cmd": "worktree_merge",
            "agent_id": &agent_id,
        }),
    )
    .await;
    assert_eq!(
        v["data"]["ok"], false,
        "merge should refuse dirty worktree: {v:?}"
    );

    // Worktree + branch intact, uncommitted file preserved.
    assert!(worktree_path.join("b.txt").exists());
    let branch_show = Command::new("git")
        .current_dir(&repo)
        .args(["show-ref", "--verify", "refs/heads/loom/dirty"])
        .status()
        .unwrap();
    assert!(branch_show.success(), "branch was deleted despite dirty block");
    let st = state.lock().await;
    let agent = st.agents.get(&agent_id).unwrap();
    assert!(!agent.worktree_path.is_empty());
    assert!(!agent.worktree_merged);
}

/// With auto-checkpoint enabled, a dirty worktree should be committed
/// synchronously BEFORE the merge runs. Verifies Fix D.
#[tokio::test]
async fn worktree_merge_runs_pre_merge_checkpoint_synchronously() {
    if !git_available() {
        eprintln!("git not available, skipping");
        return;
    }
    let tmp = tempfile::tempdir().unwrap();
    let repo = tmp.path().to_path_buf();
    init_repo(&repo);

    let (addr, state, agent_id) = spawn_server_with_agent(&repo).await;

    let v = post(
        addr,
        json!({
            "cmd": "worktree_create",
            "agent_id": &agent_id,
            "branch": "loom/autocp",
            "base": "main"
        }),
    )
    .await;
    assert_eq!(v["ok"], true);
    let worktree_path = PathBuf::from(v["data"]["path"].as_str().unwrap());

    // Enable auto-checkpoint.
    {
        let mut st = state.lock().await;
        let cell = st.agents.get_mut(&agent_id).unwrap();
        cell.worktree_auto_checkpoint = true;
    }

    // Leave uncommitted work in the worktree.
    std::fs::write(worktree_path.join("uncommitted.txt"), "hello\n").unwrap();

    let base_before = String::from_utf8(
        Command::new("git")
            .current_dir(&repo)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap()
            .stdout,
    )
    .unwrap()
    .trim()
    .to_string();

    let v = post(
        addr,
        json!({
            "cmd": "worktree_merge",
            "agent_id": &agent_id,
        }),
    )
    .await;
    assert_eq!(
        v["ok"], true,
        "merge should succeed after synchronous checkpoint: {v:?}"
    );

    // Main advanced — the checkpointed work landed on main.
    let base_after = String::from_utf8(
        Command::new("git")
            .current_dir(&repo)
            .args(["rev-parse", "HEAD"])
            .output()
            .unwrap()
            .stdout,
    )
    .unwrap()
    .trim()
    .to_string();
    assert_ne!(base_before, base_after);

    // uncommitted.txt now lives on main.
    let main_has_file = Command::new("git")
        .current_dir(&repo)
        .args(["cat-file", "-e", "HEAD:uncommitted.txt"])
        .status()
        .unwrap()
        .success();
    assert!(
        main_has_file,
        "uncommitted work was not preserved through the pre-merge checkpoint"
    );
}
