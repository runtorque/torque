# TORQUE:785 — Supervisor reliability + observability/control: swipe findings + design

Engineer: Courier (runtime-pty + quality-observability). Backend only; frontend = TORQUE:786 (Panelsmith).
Diagnose/design-first; this doc is the handup for Torqly sign-off BEFORE wiring the delicate restart.

## A. SWIPE / DIAGNOSIS (post-#308 / 158d270d)

### What #308 actually fixed — the daemon↔supervisor CHANNEL (not the process lifecycle)
- pty_supervisor_client: 30s per-request timeout (no _request_lock deadlock); connection-generation guard (no connect→disconnect oscillation from a stale read_loop).
- server: failed-write replay moved to a background task (HTTP bind no longer blocked); UI WebSocket heartbeat=30 prunes half-open clients.
- ws.js: client-side liveness watchdog force-closes zombie sockets → triggers reconnect.
- pty_supervisor: master fd non-blocking; reads select-gated (EAGAIN); writes bounded by WRITE_DEADLINE_SECONDS → "write_backpressure" error instead of an unbounded os.write stall (one hung agent could previously wedge the whole shared channel).
Net: #308 hardened the CHANNEL against deadlock/oscillation/backpressure, and the daemon-restart→supervisor-survives→client-reconnect+resubscribe path. It did NOT address supervisor PROCESS death.

### Architecture facts (relevant to the design)
- Supervisor is a SEPARATE process (spawn_detached: start_new_session=True, close_fds=True). It owns every PTY master fd + child subprocess. Children are spawned start_new_session (own pgid), managed via os.killpg.
- It is deliberately built to OUTLIVE the daemon: daemon os.execv/crash → supervisor persists → new daemon ensure_running() pings the socket; the client reconnect loop resubscribes; on_reconnect detects a fresh pid.
- Client already exposes: is_connected(), last_op_latency_ms, last_supervisor_pid, on_reconnect hook.
- Wire surface already exposes: ping → {version, pid, started_at}; list → {supervisor:{pid,started_at}, sessions:[{session_id,cell_id,pid,alive,cols,rows,total_bytes,started_at,shell_argv,cwd,...}]}.
- doctor report already has a pty_supervisor section + bridge.supervisor_write_breaker_snapshot (open input-write breakers / stuck sessions).
- Banners already exist: supervisor_unavailable (startup fallback) and supervisor_restarted (on_reconnect fresh-pid → "open terminals were …").
- Logs on disk: daemon = DATA_DIR/torque.log (already tailed via _tail_log_entries, server.py:21100); supervisor = DATA_DIR/pty_supervisor.log (NO read surface yet).

### GAPS (what #308 does NOT cover)
1. SUPERVISOR PROCESS DEATH = WORKER LOSS. Both paths drop workers:
   - graceful (SIGTERM/SIGINT): _serve signal handler → _graceful → supervisor.shutdown() → _terminate_session per session → os.killpg(SIGHUP→SIGTERM→SIGKILL). Children KILLED.
   - crash: OS closes the supervisor's master fds → child slave gets EIO/SIGHUP → PTY collapses → children die.
   There is NO existing path where the supervisor restarts and workers survive. "Self-healing" today = daemon-dies case only.
2. NO crash supervision of the supervisor itself. ensure_running() runs only at daemon startup. If the supervisor dies while the daemon lives, nothing respawns it; the client reconnect loop retries a socket with no listener forever. (A respawn would only restore NEW-session capability — the live workers are already gone.)
3. NO safe user-initiated restart (the ask).
4. OBSERVABILITY thin/opaque: status is piecemeal (pid/started_at/latency/connected) with no consolidated health surface; metrics essentially absent (no op counts, error/backpressure counters, reconnect counts, session peak); supervisor.log has no read surface.

## B. DESIGN — the 5 backend deliverables

### (c) SAFE USER-INITIATED RESTART — delicate, the sign-off item
RECOMMENDED: in-place os.execv RE-EXEC of the supervisor (NOT kill+respawn, NOT SCM_RIGHTS handoff).
Why re-exec preserves live workers:
- execv keeps the SAME pid → children stay parented (waitpid still works post-exec).
- pty.openpty() master fds are NOT O_CLOEXEC → they survive execv (verify; explicitly clear FD_CLOEXEC to be safe). Children never get SIGHUP → workers PRESERVED.
- The existing finalize fallback (session.process is None → os.waitpid by pid, pty_supervisor.py ~606) is exactly the post-exec adoption path — scaffolding already present.
- #308 reconnect substrate bridges the brief socket-rebind gap (client reconnect 0.5s + resubscribe_all).
- vs SCM_RIGHTS fd-handoff to a fresh successor: more complex (fd passing, two live processes, adoption race), no benefit here.
Mechanism:
1. Explicit wire op `restart` (daemon→supervisor; gives the daemon timing control + an ack) — preferred over a bare signal.
2. On restart the supervisor: (a) snapshots its session table to a DATA_DIR state file (session_id, cell_id, master_fd int, child pid, cols, rows, cwd, shell_argv, bootstrap_dir, started_at, total_bytes; optionally bounded tail of buffer); (b) clears FD_CLOEXEC on each master_fd; (c) stops the asyncio server + cancels read loops WITHOUT terminating sessions (must NOT call shutdown()); (d) os.execv(sys.executable, [-m torque.pty_supervisor, --data-dir …, --adopt-state <file>]).
3. Post-exec _serve with --adopt-state: rebuild SupervisorSession objects around inherited master fds (process=None), restart read loops, rebind socket, rewrite pid file (same pid), delete the state file.
4. Daemon client reconnects (socket reappears) + resubscribes. NOTE: pid is unchanged across execv, so drive the supervisor_restarted banner off a restart epoch/nonce (bump in state), not pid change.
Edge cases for sign-off:
- A few bytes of child output during the exec gap may be lost (kernel PTY buffer holds some; acceptable for a rare user action).
- In-memory scrollback lost unless persisted (frontend keeps its own; option to persist bounded buffer).
- Adopt failure (corrupt state / fd gone) → fall back to a clean supervisor (workers lost, daemon recovers) + LOUD health event; never hang.
- Mark supervisor status "restarting"; reject/queue ops during the window.
- Profile mode (PROFILE_SKIP_PTY) has no supervisor → restart is N/A.

### (b) STATUS reporting (drives the status-bar; consumed by 786)
Daemon-side supervisor-health projection in the /app state the frontend reads. Fields: state ∈ {up, degraded, restarting, down, unavailable, na_profile}; supervisor_pid; uptime; connected; last_op_latency_ms; last_reconnect_at; reconnect_count; session_count. State derived from is_connected() + the liveness watchdog (below).

### (d) RUNTIME METRICS
Supervisor-side in-memory counters, cheap, exposed via a `metrics` op (or folded into list/ping): ops_total by type; errors_total by code (incl write_backpressure); bytes_written/bytes_read; sessions_current/peak/created_total; read_loop_failures; write_deadline_hits. Daemon-side: reconnect_count, last_op_latency_ms (exists), time_since_last_successful_op. Surfaced in the /app projection + doctor report.

### (e) LOG ACCESS
Reuse the existing _tail_log_entries machinery (server.py:21100). Add a bounded read/tail surface parameterized by target ∈ {daemon: DATA_DIR/torque.log, supervisor: DATA_DIR/pty_supervisor.log}. Read-only, bounded line count; no streaming for v1. (Frontend View→Show logs picker = 786.)

### Hardening rec (from the swipe, propose as part of (b) or a small follow-up)
Daemon-side supervisor liveness watchdog: detect process death (pid not alive OR repeated reconnect failures), auto-respawn via ensure_running, emit a health event + drive the status surface. Respawn restores NEW-session capability and surfaces the worker loss honestly (add an explicit per-session "supervisor_lost" exit reason rather than a silent collapse).

## C. BUILD SEQUENCING (after sign-off)
1. Observability slice (LOW risk, no live-PTY exposure): (b) status + (d) metrics + (e) logs — additive read surfaces, independently reviewable, shippable without touching restart.
2. Restart slice (the delicate one): (c) re-exec adoption, built + unit-tested in isolation (a test supervisor exercising snapshot→execv→adopt with a dummy child), user exercises live on a real relaunch.
3. Watchdog/auto-respawn hardening: with the observability slice or its own follow-up.
Independent review each slice; no self-merge; verify-first merge (gh PR-squash fallback expected until 781 lands the zero-ee-delta fix).

## Sign-off questions for Torqly
1. Restart approach: in-place os.execv re-exec (recommended) vs SCM_RIGHTS handoff vs accept-worker-loss drain-restart?
2. Split into 2+ reviewed slices (observability first, restart second)? (recommended)
3. Scrollback across restart: persist bounded buffer, or accept loss (frontend keeps its own)?
4. Liveness watchdog + auto-respawn: in-scope for 785 or a separate follow-up?
