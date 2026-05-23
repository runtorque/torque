#!/usr/bin/env python3
"""Torque perf-wave profile harness.

Runs a short-lived standalone Torque daemon, seeds synthetic in-memory agents, sends
provider-free events through /events, and writes JSON/Markdown evidence for the
N=10/20/30 closeout gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import aiohttp
except ImportError as exc:  # pragma: no cover - exercised by operator envs
    aiohttp = None
    _AIOHTTP_IMPORT_ERROR = exc
else:
    _AIOHTTP_IMPORT_ERROR = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from torque.profiling import percentile, summarize  # noqa: E402

DEFAULT_MATRIX = "10,20,30"
DEFAULT_EVENT_RATE = 2.5
DEFAULT_EVENT_MIX = {
    "heartbeat": 0.26,
    "progress": 0.24,
    "activity_change": 0.14,
    "tool_start": 0.08,
    "tool_end": 0.08,
    "derive": 0.06,
    "done": 0.05,
    "waiting": 0.03,
    "cost_update": 0.03,
    "session_start": 0.015,
    "session_end": 0.015,
}
COMPARE_METRICS: list[tuple[str, str, bool]] = [
    ("event_loss_count", "Event loss", True),
    ("sqlite_write_p95_ms", "SQLite write p95", True),
    ("event_loop_lag_p95_ms", "Event loop lag p95", True),
    ("ws_broadcast_p95_ms", "WS broadcast p95", True),
    ("ws_payload_p95_bytes", "WS payload p95", True),
    ("event_endpoint_p95_ms", "Event endpoint p95", True),
    ("snapshot_max_bytes", "Snapshot max bytes", True),
]


def require_aiohttp() -> None:
    if aiohttp is None:  # pragma: no cover - depends on operator env
        raise SystemExit(
            "profile_harness.py requires aiohttp (the same dependency used by Torque)."
        ) from _AIOHTTP_IMPORT_ERROR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_matrix(value: str) -> list[int]:
    matrix: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        n = int(part)
        if n <= 0:
            raise ValueError("matrix values must be positive")
        matrix.append(n)
    if not matrix:
        raise ValueError("matrix must contain at least one N value")
    return matrix


def parse_event_mix(value: str | None) -> dict[str, float]:
    if not value:
        return dict(DEFAULT_EVENT_MIX)
    mix: dict[str, float] = {}
    for part in value.split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise ValueError(f"invalid event mix item {part!r}; expected kind=weight")
        key, raw_weight = part.split("=", 1)
        weight = float(raw_weight)
        if weight < 0:
            raise ValueError("event mix weights must be non-negative")
        mix[key.strip()] = weight
    if not any(mix.values()):
        raise ValueError("event mix must contain a positive weight")
    return mix


def choose_weighted(rng: random.Random, weights: dict[str, float]) -> str:
    total = sum(max(0.0, weight) for weight in weights.values())
    pick = rng.random() * total
    cumulative = 0.0
    for key, weight in weights.items():
        cumulative += max(0.0, weight)
        if pick <= cumulative:
            return key
    return next(reversed(weights))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_git(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def stat_sample(metrics: dict[str, Any], name: str, field: str = "p95") -> float:
    return float(
        metrics.get("samples", {}).get(name, {}).get(field, 0.0) or 0.0
    )


def counter(metrics: dict[str, Any], name: str) -> int:
    return int(metrics.get("counters", {}).get(name, 0) or 0)


@dataclass
class ClientMetrics:
    sent_events: int = 0
    http_errors: int = 0
    post_exceptions: int = 0
    ws_messages: int = 0
    ws_deltas: int = 0
    ws_snapshots: int = 0
    ws_bytes: list[float] = field(default_factory=list)
    ws_connect_latency_ms: list[float] = field(default_factory=list)
    event_post_latency_ms: list[float] = field(default_factory=list)
    first_ws_seq: int = 0
    last_ws_seq: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent_events": self.sent_events,
            "http_errors": self.http_errors,
            "post_exceptions": self.post_exceptions,
            "ws_messages": self.ws_messages,
            "ws_deltas": self.ws_deltas,
            "ws_snapshots": self.ws_snapshots,
            "first_ws_seq": self.first_ws_seq,
            "last_ws_seq": self.last_ws_seq,
            "ws_message_bytes": summarize(self.ws_bytes),
            "ws_connect_latency_ms": summarize(self.ws_connect_latency_ms),
            "event_post_latency_ms": summarize(self.event_post_latency_ms),
        }


@dataclass
class DaemonHandle:
    proc: subprocess.Popen
    port: int
    data_dir: Path
    stdout_path: Path
    stderr_path: Path

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def start_daemon(data_dir: Path, *, port: int | None = None) -> DaemonHandle:
    port = port or free_port()
    data_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = data_dir / "daemon.stdout.log"
    stderr_path = data_dir / "daemon.stderr.log"
    env = os.environ.copy()
    env.update({
        "TORQUE_STANDALONE": "1",
        "TORQUE_PORT": str(port),
        "TORQUE_DATA_DIR": str(data_dir),
        "TORQUE_PROFILE": "perf-harness",
        "TORQUE_PERF_PROFILE": "1",
        "TORQUE_PROFILE_ENABLED": "1",
        "TORQUE_PROFILE_SKIP_PTY": "1",
        "TORQUE_PROFILE_SLOW_CALLBACK_MS": env.get(
            "TORQUE_PROFILE_SLOW_CALLBACK_MS", "25"),
        "TORQUE_DEFAULT_CMD": "synthetic-agent",
        "PYTHONPATH": f"{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}",
    })
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "torque.py")],
        cwd=REPO_ROOT,
        env=env,
        stdout=stdout,
        stderr=stderr,
        text=True,
    )
    stdout.close()
    stderr.close()
    return DaemonHandle(proc, port, data_dir, stdout_path, stderr_path)


def stop_daemon(handle: DaemonHandle) -> None:
    proc = handle.proc
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    # The daemon spawns detached event-ingest / pty-supervisor sidecars into
    # this temp data dir; they outlive the daemon's SIGTERM. Reap them so the
    # harness does not leak processes toward the per-uid ceiling.
    try:
        from torque import sidecar_reaper

        sidecar_reaper.reap_sidecars_for_data_dir(handle.data_dir)
    except Exception:
        pass


async def wait_for_daemon(handle: DaemonHandle, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    url = f"{handle.base_url}/api/profile"
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            if handle.proc.poll() is not None:
                raise RuntimeError(
                    f"Torque daemon exited early with {handle.proc.returncode}. "
                    f"stderr: {handle.stderr_path.read_text(errors='ignore')[-2000:]}"
                )
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return
                    last_error = f"HTTP {resp.status}"
            except Exception as exc:
                last_error = str(exc)
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {url}: {last_error}")


async def post_json(session: aiohttp.ClientSession, url: str,
                    payload: dict[str, Any], *, headers: dict[str, str] | None = None,
                    timeout: float = 10.0) -> dict[str, Any]:
    async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"POST {url} failed HTTP {resp.status}: {text[:500]}")
        if not text:
            return {}
        return json.loads(text)


async def get_json(session: aiohttp.ClientSession, url: str,
                   timeout: float = 10.0) -> dict[str, Any]:
    async with session.get(url, timeout=timeout) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"GET {url} failed HTTP {resp.status}: {text[:500]}")
        return json.loads(text)


def build_event_payload(event_type: str, *, agent_index: int, seq: int,
                        rng: random.Random) -> dict[str, Any]:
    detail_targets = [
        "editing server.py",
        "updating board state",
        "writing SQLite row",
        "broadcasting websocket delta",
        "checking worktree boundary",
        "rendering task card",
    ]
    payload: dict[str, Any] = {
        "source": "torque-profile-harness",
        "event_type": event_type,
        "event_id": f"agent-{agent_index}:{seq}",
        "timestamp": time.time(),
    }
    if event_type == "session_start":
        payload.update({
            "session_id": f"synthetic-session-{agent_index}-{seq}",
            "model": "synthetic-profile",
        })
    elif event_type == "session_end":
        payload.update({
            "summary": f"synthetic closeout summary {seq}",
            "reason": "completed",
        })
    elif event_type == "activity_change":
        activity = rng.choice(["thinking", "tool_call", "writing", "waiting", ""])
        payload.update({
            "activity": activity,
            "detail": rng.choice(detail_targets) if activity else "",
        })
    elif event_type == "tool_start":
        tool = rng.choice(["Bash", "Read", "Edit", "Grep", "WebFetch"])
        payload.update({
            "tool": tool,
            "detail": f"Using {tool}: {rng.choice(detail_targets)}",
        })
    elif event_type == "tool_end":
        payload.update({"tool": rng.choice(["Bash", "Read", "Edit", "Grep"]), "success": True})
    elif event_type == "progress":
        payload.update({"detail": f"progress ping: {rng.choice(detail_targets)}"})
    elif event_type == "heartbeat":
        payload.update({"detail": "synthetic heartbeat"})
    elif event_type == "derive":
        payload.update({
            "task": f"synthetic follow-up {agent_index}-{seq}",
            "summary": "derived bounded follow-up task",
        })
    elif event_type == "done":
        payload.update({"summary": "completed synthetic task slice"})
    elif event_type == "waiting":
        payload.update({"reason": "synthetic permission checkpoint"})
    elif event_type == "cost_update":
        payload.update({
            "input_tokens": rng.randint(200, 1200),
            "output_tokens": rng.randint(50, 400),
        })
    elif event_type == "error":
        payload.update({"error": "synthetic non-fatal error"})
    return payload


async def post_event(session: aiohttp.ClientSession, base_url: str, cell_id: str,
                     payload: dict[str, Any], metrics: ClientMetrics) -> None:
    started = time.perf_counter()
    metrics.sent_events += 1
    try:
        async with session.post(
            f"{base_url}/events",
            json=payload,
            headers={"X-Torque-Cell-Id": cell_id},
            timeout=5,
        ) as resp:
            await resp.read()
            if resp.status != 200:
                metrics.http_errors += 1
    except Exception:
        metrics.post_exceptions += 1
    finally:
        metrics.event_post_latency_ms.append((time.perf_counter() - started) * 1000.0)


async def run_agent_events(session: aiohttp.ClientSession, base_url: str,
                           cell_id: str, *, agent_index: int, stop_at: float,
                           rate: float, weights: dict[str, float], seed: int,
                           metrics: ClientMetrics) -> None:
    rng = random.Random(seed + agent_index * 7919)
    seq = 0

    async def emit(kind: str) -> None:
        nonlocal seq
        seq += 1
        await post_event(
            session,
            base_url,
            cell_id,
            build_event_payload(kind, agent_index=agent_index, seq=seq, rng=rng),
            metrics,
        )

    await emit("session_start")
    while time.monotonic() < stop_at:
        interval = rng.expovariate(rate) if rate > 0 else 0.1
        await asyncio.sleep(min(interval, max(0.0, stop_at - time.monotonic())))
        if time.monotonic() >= stop_at:
            break
        kind = choose_weighted(rng, weights)
        await emit(kind)
    await emit("session_end")


async def collect_ws(base_url: str, stop_event: asyncio.Event,
                     metrics: ClientMetrics) -> None:
    ws_url = base_url.replace("http://", "ws://") + "/ws"
    started = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, heartbeat=20) as ws:
                metrics.ws_connect_latency_ms.append(
                    (time.perf_counter() - started) * 1000.0)
                while not stop_event.is_set():
                    try:
                        msg = await ws.receive(timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = msg.data
                        metrics.ws_messages += 1
                        metrics.ws_bytes.append(float(len(data.encode("utf-8"))))
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        seq = int(payload.get("seq", 0) or 0)
                        if seq:
                            if not metrics.first_ws_seq:
                                metrics.first_ws_seq = seq
                            metrics.last_ws_seq = seq
                        if payload.get("type") == "state":
                            metrics.ws_snapshots += 1
                        elif payload.get("type") == "delta":
                            metrics.ws_deltas += 1
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
    except Exception:
        # WS collection is evidence, not the load generator. The missing
        # snapshot/delta counts make the issue visible in the output.
        return


def start_py_spy(pid: int, output: Path, duration: float) -> tuple[subprocess.Popen | None, dict[str, Any]]:
    py_spy = shutil.which("py-spy")
    info: dict[str, Any] = {
        "available": bool(py_spy),
        "output": str(output),
        "format": "speedscope",
    }
    if not py_spy:
        info["status"] = "missing"
        return None, info
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        py_spy,
        "record",
        "--pid", str(pid),
        "--duration", str(max(1, int(duration))),
        "--rate", "100",
        "--format", "speedscope",
        "--output", str(output),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        info["status"] = "started"
        info["cmd"] = cmd
        return proc, info
    except Exception as exc:
        info["status"] = "failed_to_start"
        info["error"] = str(exc)
        return None, info


def finish_py_spy(proc: subprocess.Popen | None, info: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    if proc is None:
        return info
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
    info.update({
        "returncode": proc.returncode,
        "stdout_tail": (stdout or "")[-1000:],
        "stderr_tail": (stderr or "")[-1000:],
        "status": "ok" if proc.returncode == 0 else "failed",
    })
    return info


async def seed_agents(session: aiohttp.ClientSession, base_url: str, n: int,
                      *, group: str) -> list[str]:
    payload = {
        "group": group,
        "prefix": f"perf-n{n}",
        "count": n,
        "reset": True,
        "directory": str(REPO_ROOT),
        "agent_type": "claude-code",
    }
    response = await post_json(session, f"{base_url}/api/profile/synthetic_agents", payload)
    ids = response.get("data", {}).get("agent_ids", [])
    if len(ids) != n:
        raise RuntimeError(f"requested {n} synthetic agents, got {len(ids)}")
    return [str(agent_id) for agent_id in ids]


async def run_single_n(n: int, args: argparse.Namespace, *, artifact_dir: Path) -> dict[str, Any]:
    duration = float(args.duration)
    rate = float(args.rate)
    weights = parse_event_mix(args.event_mix)
    data_parent = Path(args.data_dir).expanduser() if args.data_dir else None
    tmp_ctx = None
    if data_parent:
        data_dir = data_parent / f"n{n}-{int(time.time())}"
        data_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix=f"torque-perf-n{n}-")
        data_dir = Path(tmp_ctx.name)

    handle = start_daemon(data_dir, port=args.port if len(args.matrix_values) == 1 else None)
    pyspy_proc = None
    pyspy_info: dict[str, Any] = {"available": False, "status": "disabled"}
    started_at = utc_now()
    run_started = time.perf_counter()
    try:
        await wait_for_daemon(handle, timeout=float(args.startup_timeout))
        pyspy_path = artifact_dir / f"pyspy-n{n}.speedscope.json"
        if not args.no_py_spy:
            pyspy_proc, pyspy_info = start_py_spy(
                handle.proc.pid, pyspy_path, duration + 5)
        timeout = aiohttp.ClientTimeout(total=max(10.0, duration + 15.0))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await post_json(session, f"{handle.base_url}/api/profile/reset", {})
            agent_ids = await seed_agents(session, handle.base_url, n, group=args.group)
            # Isolate load metrics from synthetic setup writes while preserving
            # the seeded cells for snapshot sizing and event routing.
            await post_json(session, f"{handle.base_url}/api/profile/reset", {})
            client_metrics = ClientMetrics()
            ws_stop = asyncio.Event()
            ws_task = asyncio.create_task(collect_ws(handle.base_url, ws_stop, client_metrics))
            await asyncio.sleep(float(args.ws_warmup))
            stop_at = time.monotonic() + duration
            await asyncio.gather(*[
                run_agent_events(
                    session,
                    handle.base_url,
                    cell_id,
                    agent_index=index,
                    stop_at=stop_at,
                    rate=rate,
                    weights=weights,
                    seed=int(args.seed),
                    metrics=client_metrics,
                )
                for index, cell_id in enumerate(agent_ids)
            ])
            await asyncio.sleep(float(args.broadcast_drain))
            ws_stop.set()
            await ws_task
            profile_response = await get_json(
                session,
                f"{handle.base_url}/api/profile?cprofile_limit={int(args.cprofile_limit)}",
            )
            server_metrics = profile_response.get("data", {})
    finally:
        pyspy_info = finish_py_spy(pyspy_proc, pyspy_info)
        stop_daemon(handle)
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    elapsed = time.perf_counter() - run_started
    accepted = counter(server_metrics, "events_accepted")
    sent = client_metrics.sent_events
    event_loss = max(0, sent - accepted)
    samples = server_metrics.get("samples", {})
    acceptance = {
        "event_loss_count": event_loss,
        "snapshot_json_size_bytes": samples.get("snapshot_json_bytes", {}),
        "ws_connect_latency_ms": samples.get("ws_connect_latency_ms", {}),
        "event_loop_blocking_ms": samples.get("event_loop_lag_ms", {}),
        "sqlite_write_latency_ms": samples.get("sqlite_write_ms", {}),
        "ws_payload_size_bytes": samples.get("ws_delta_payload_bytes", {}),
        "ws_broadcast_latency_ms": samples.get("ws_broadcast_ms", {}),
        "event_endpoint_latency_ms": samples.get("event_endpoint_ms", {}),
    }
    hot_path = extract_hot_path(server_metrics, event_loss)
    return {
        "n": n,
        "started_at": started_at,
        "duration_sec": duration,
        "elapsed_sec": elapsed,
        "event_rate_per_agent_sec": rate,
        "event_mix": weights,
        "daemon": {
            "pid": handle.proc.pid,
            "port": handle.port,
            "data_dir": str(data_dir),
            "stdout": str(handle.stdout_path),
            "stderr": str(handle.stderr_path),
        },
        "client": client_metrics.as_dict(),
        "server": server_metrics,
        "py_spy": pyspy_info,
        "acceptance": acceptance,
        "hot_path": hot_path,
    }


def extract_hot_path(metrics: dict[str, Any], event_loss: int) -> dict[str, float]:
    return {
        "event_loss_count": float(event_loss),
        "sqlite_write_p95_ms": stat_sample(metrics, "sqlite_write_ms", "p95"),
        "event_loop_lag_p95_ms": stat_sample(metrics, "event_loop_lag_ms", "p95"),
        "ws_broadcast_p95_ms": stat_sample(metrics, "ws_broadcast_ms", "p95"),
        "ws_payload_p95_bytes": stat_sample(metrics, "ws_delta_payload_bytes", "p95"),
        "event_endpoint_p95_ms": stat_sample(metrics, "event_endpoint_ms", "p95"),
        "snapshot_max_bytes": stat_sample(metrics, "snapshot_json_bytes", "max"),
    }


def run_index(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(run.get("n", 0)): run for run in report.get("runs", [])}


def compare_reports(baseline: dict[str, Any], current: dict[str, Any],
                    *, threshold_pct: float) -> dict[str, Any]:
    baseline_runs = run_index(baseline)
    current_runs = run_index(current)
    rows = []
    regressions = []
    wins = []
    for n, current_run in sorted(current_runs.items()):
        baseline_run = baseline_runs.get(n)
        if not baseline_run:
            continue
        base_hot = baseline_run.get("hot_path") or extract_hot_path(
            baseline_run.get("server", {}),
            int(baseline_run.get("acceptance", {}).get("event_loss_count", 0) or 0),
        )
        current_hot = current_run.get("hot_path", {})
        for key, label, lower_is_better in COMPARE_METRICS:
            base_value = float(base_hot.get(key, 0.0) or 0.0)
            current_value = float(current_hot.get(key, 0.0) or 0.0)
            if base_value == 0:
                delta_pct = 0.0 if current_value == 0 else 100.0
            else:
                delta_pct = ((current_value - base_value) / base_value) * 100.0
            status = "flat"
            if lower_is_better:
                if delta_pct > threshold_pct:
                    status = "regression"
                elif delta_pct < -threshold_pct:
                    status = "win"
            else:
                if delta_pct < -threshold_pct:
                    status = "regression"
                elif delta_pct > threshold_pct:
                    status = "win"
            row = {
                "n": n,
                "metric": key,
                "label": label,
                "baseline": base_value,
                "current": current_value,
                "delta_pct": delta_pct,
                "status": status,
            }
            rows.append(row)
            if status == "regression":
                regressions.append(row)
            elif status == "win":
                wins.append(row)
    return {
        "threshold_pct": threshold_pct,
        "rows": rows,
        "regressions": regressions,
        "wins": wins,
    }


def build_report(args: argparse.Namespace, runs: list[dict[str, Any]],
                 comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "torque-perf-profile",
        "mode": args.mode,
        "generated_at": utc_now(),
        "repo": {
            "root": str(REPO_ROOT),
            "branch": run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "head": run_git(["rev-parse", "HEAD"]),
            "status_short": run_git(["status", "--short"]),
        },
        "config": {
            "matrix": [run["n"] for run in runs],
            "duration_sec": float(args.duration),
            "event_rate_per_agent_sec": float(args.rate),
            "group": args.group,
            "startup_timeout_sec": float(args.startup_timeout),
            "regression_threshold_pct": float(args.regression_threshold),
            "py_spy_enabled": not args.no_py_spy,
        },
        "runs": runs,
        "comparison": comparison or {},
    }


def format_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return ""


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Torque perf profile harness report",
        "",
        f"- Mode: `{report.get('mode', '')}`",
        f"- Generated: `{report.get('generated_at', '')}`",
        f"- Repo: `{report.get('repo', {}).get('branch', '')}` @ `{report.get('repo', {}).get('head', '')[:12]}`",
        f"- Matrix: `{report.get('config', {}).get('matrix', [])}`",
        f"- Duration: `{report.get('config', {}).get('duration_sec', 0)}` sec/run",
        "",
        "## Acceptance evidence",
        "",
        "| N | sent | accepted | loss | snapshot max bytes | WS connect p95 ms | loop lag p95 ms | SQLite p95 ms | WS payload p95 bytes | WS broadcast p95 ms |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in report.get("runs", []):
        server = run.get("server", {})
        acceptance = run.get("acceptance", {})
        client = run.get("client", {})
        lines.append(
            "| {n} | {sent} | {accepted} | {loss} | {snapshot} | {wsconn} | {lag} | {sqlite} | {payload} | {broadcast} |".format(
                n=run.get("n"),
                sent=client.get("sent_events", 0),
                accepted=counter(server, "events_accepted"),
                loss=acceptance.get("event_loss_count", 0),
                snapshot=format_num(acceptance.get("snapshot_json_size_bytes", {}).get("max", 0), 0),
                wsconn=format_num(acceptance.get("ws_connect_latency_ms", {}).get("p95", 0)),
                lag=format_num(acceptance.get("event_loop_blocking_ms", {}).get("p95", 0)),
                sqlite=format_num(acceptance.get("sqlite_write_latency_ms", {}).get("p95", 0)),
                payload=format_num(acceptance.get("ws_payload_size_bytes", {}).get("p95", 0), 0),
                broadcast=format_num(acceptance.get("ws_broadcast_latency_ms", {}).get("p95", 0)),
            )
        )
    lines.extend(["", "## CPU profile top functions", ""])
    for run in report.get("runs", []):
        lines.append(f"### N={run.get('n')}")
        lines.append("")
        lines.append("| rank | cumulative sec | total sec | calls | function |")
        lines.append("|---:|---:|---:|---:|---|")
        for idx, row in enumerate(run.get("server", {}).get("cprofile_top", [])[:10], start=1):
            lines.append(
                f"| {idx} | {format_num(row.get('cumulative_sec', 0), 4)} | "
                f"{format_num(row.get('total_sec', 0), 4)} | {row.get('calls', 0)} | "
                f"`{row.get('function', '')}` |"
            )
        lines.append("")
    comparison = report.get("comparison") or {}
    if comparison.get("rows"):
        lines.extend([
            "## Baseline vs current delta",
            "",
            f"Threshold: ±{comparison.get('threshold_pct', 0)}%",
            "",
            "| N | metric | baseline | current | delta % | status |",
            "|---:|---|---:|---:|---:|---|",
        ])
        for row in comparison.get("rows", []):
            lines.append(
                f"| {row.get('n')} | {row.get('label')} | "
                f"{format_num(row.get('baseline', 0), 3)} | "
                f"{format_num(row.get('current', 0), 3)} | "
                f"{format_num(row.get('delta_pct', 0), 1)} | {row.get('status')} |"
            )
        lines.append("")
    lines.extend([
        "## Py-spy artifacts",
        "",
        "| N | status | output |",
        "|---:|---|---|",
    ])
    for run in report.get("runs", []):
        pyspy = run.get("py_spy", {})
        lines.append(
            f"| {run.get('n')} | {pyspy.get('status', '')} | `{pyspy.get('output', '')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def write_artifacts(report: dict[str, Any], output: Path, report_path: Path | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(report), encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    require_aiohttp()
    args.matrix_values = parse_matrix(args.matrix)
    artifact_dir = Path(args.artifact_dir).expanduser() if args.artifact_dir else Path(args.output).expanduser().with_suffix("").parent / "artifacts"
    runs = []
    for n in args.matrix_values:
        print(f"[perf] running N={n} duration={args.duration}s rate={args.rate}/agent/s", flush=True)
        runs.append(await run_single_n(n, args, artifact_dir=artifact_dir))
    comparison = None
    if args.mode == "delta":
        baseline_path = Path(args.baseline).expanduser()
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        comparison = compare_reports(
            baseline,
            {"runs": runs},
            threshold_pct=float(args.regression_threshold),
        )
    report = build_report(args, runs, comparison=comparison)
    write_artifacts(
        report,
        Path(args.output).expanduser(),
        Path(args.report).expanduser() if args.report else None,
    )
    if comparison and comparison.get("regressions") and args.fail_on_regression:
        print(f"[perf] regressions detected: {len(comparison['regressions'])}", file=sys.stderr)
        return 2
    print(f"[perf] wrote {args.output}", flush=True)
    if args.report:
        print(f"[perf] wrote {args.report}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["baseline", "delta"], default="baseline")
    parser.add_argument("--matrix", default=os.environ.get("PERF_MATRIX", DEFAULT_MATRIX))
    parser.add_argument("--duration", type=float, default=float(os.environ.get("PERF_DURATION", "15")))
    parser.add_argument("--rate", type=float, default=float(os.environ.get("PERF_EVENT_RATE", DEFAULT_EVENT_RATE)))
    parser.add_argument("--event-mix", default=os.environ.get("PERF_EVENT_MIX", ""))
    parser.add_argument("--group", default=os.environ.get("PERF_GROUP", "perf-harness"))
    parser.add_argument("--output", default=os.environ.get("PERF_OUTPUT", "tests/perf/baseline.json"))
    parser.add_argument("--report", default=os.environ.get("PERF_REPORT", ""))
    parser.add_argument("--baseline", default=os.environ.get("PERF_BASELINE", "tests/perf/baseline.json"))
    parser.add_argument("--artifact-dir", default=os.environ.get("PERF_ARTIFACT_DIR", ""))
    parser.add_argument("--data-dir", default=os.environ.get("PERF_DATA_DIR", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PERF_PORT", "0") or 0))
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--ws-warmup", type=float, default=0.25)
    parser.add_argument("--broadcast-drain", type=float, default=1.4)
    parser.add_argument("--seed", type=int, default=int(os.environ.get("PERF_SEED", "12345")))
    parser.add_argument("--cprofile-limit", type=int, default=30)
    parser.add_argument("--regression-threshold", type=float, default=float(os.environ.get("PERF_REGRESSION_THRESHOLD", "20")))
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--no-py-spy", action="store_true", default=os.environ.get("PERF_NO_PY_SPY", "").lower() in {"1", "true", "yes"})
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "delta" and not args.baseline:
        parser.error("--baseline is required in delta mode")
    if not args.report:
        args.report = str(Path(args.output).with_suffix(".md"))
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
