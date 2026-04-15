"""Thin Python runtime for the Rust↔iTerm2 bridge mode."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

import aiohttp
from aiohttp import web

from .config import SCRIPT_DIR, log
from .iterm2_bridge_core import ITerm2BridgeCore
from .state import AgentCell, GroupSettings, MatrixState

RUST_BRIDGE_DEFAULT_PROFILE = "rust-bridge"
RUST_BRIDGE_DEFAULT_PORT = 18934
RUST_BRIDGE_DEFAULT_SERVICE_PORT = 19034
RUST_BRIDGE_MODE_SPAWN = "spawn"
RUST_BRIDGE_MODE_ATTACH = "attach"
STARTUP_TIMEOUT_SECS = 20.0
PROBE_TIMEOUT_SECS = 0.75
POLL_INTERVAL_SECS = 0.25


_AGENT_CELL_FIELD_NAMES = {field.name for field in fields(AgentCell)}
_TERMINAL_SYNC_FIELD_NAMES = (
    "id",
    "session_id",
    "window_id",
    "status",
    "current_process",
    "current_path",
    "current_branch",
    "git_root",
    "agent_type",
)


def _terminal_sync_payload(cell_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the terminal-owned fields Rust should accept from Python.

    Rust remains the backend-of-record for task linkage, activity, token
    counters, weaver flags, and other agent/worktree state. The Python bridge
    only owns live terminal metadata observed from iTerm2.
    """
    payload: dict[str, Any] = {}
    for name in _TERMINAL_SYNC_FIELD_NAMES:
        if name in cell_payload:
            payload[name] = cell_payload[name]
    return payload


class RustBridgeError(RuntimeError):
    """Raised when the Rust bridge runtime cannot satisfy a request."""



def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")



def _normalize_launch_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    if not mode:
        return RUST_BRIDGE_MODE_SPAWN
    if mode not in {RUST_BRIDGE_MODE_SPAWN, RUST_BRIDGE_MODE_ATTACH}:
        raise RuntimeError(
            f"Unsupported Rust bridge launch mode '{value}'. "
            f"Expected '{RUST_BRIDGE_MODE_SPAWN}' or '{RUST_BRIDGE_MODE_ATTACH}'."
        )
    return mode



def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


@dataclass(frozen=True)
class RustBridgeSettings:
    launch_mode: str
    profile: str
    rust_port: int
    bridge_port: int
    data_dir: Path
    rust_command: str
    rust_cwd: Path
    open_browser: bool

    @property
    def rust_url(self) -> str:
        return f"http://127.0.0.1:{self.rust_port}/"

    @property
    def bridge_url(self) -> str:
        return f"http://127.0.0.1:{self.bridge_port}/"



def resolve_rust_bridge_settings(
    *,
    env: Mapping[str, str] | None = None,
    script_dir: Path | None = None,
) -> RustBridgeSettings:
    env_map = dict(os.environ if env is None else env)
    launch_mode = _normalize_launch_mode(
        env_map.get("LOOM_RUST_BRIDGE_MODE")
        or env_map.get("LOOM_RUST_MODE")
        or (
            RUST_BRIDGE_MODE_ATTACH
            if _truthy(env_map.get("LOOM_RUST_ATTACH"))
            else RUST_BRIDGE_MODE_SPAWN
        )
    )
    profile = (env_map.get("LOOM_RUST_PROFILE") or "").strip() or RUST_BRIDGE_DEFAULT_PROFILE

    rust_port = int(
        (env_map.get("LOOM_RUST_PORT") or "").strip()
        or RUST_BRIDGE_DEFAULT_PORT
    )
    bridge_port = int(
        (env_map.get("LOOM_RUST_BRIDGE_PORT") or "").strip()
        or RUST_BRIDGE_DEFAULT_SERVICE_PORT
    )

    data_dir_text = (env_map.get("LOOM_RUST_DATA_DIR") or "").strip()
    if data_dir_text:
        data_dir = Path(os.path.expanduser(data_dir_text))
    else:
        home_dir = Path(env_map.get("HOME") or str(Path.home()))
        data_dir = home_dir / ".loom" / "profiles" / _slugify_profile(profile)

    rust_command = (
        env_map.get("LOOM_RUST_CMD")
        or env_map.get("LOOM_RUST_COMMAND")
        or "cargo run -p loom-app --features headless"
    ).strip()

    root = script_dir or SCRIPT_DIR
    rust_cwd_text = (env_map.get("LOOM_RUST_CWD") or "").strip()
    rust_cwd = Path(os.path.expanduser(rust_cwd_text)) if rust_cwd_text else root / "rust"

    bridge_ui = env_map.get("LOOM_BRIDGE_UI")
    open_browser = True if bridge_ui is None else _truthy(bridge_ui)

    return RustBridgeSettings(
        launch_mode=launch_mode,
        profile=profile,
        rust_port=rust_port,
        bridge_port=bridge_port,
        data_dir=data_dir,
        rust_command=rust_command,
        rust_cwd=rust_cwd,
        open_browser=open_browser,
    )



def build_rust_server_env(
    settings: RustBridgeSettings,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env_map = dict(os.environ if base_env is None else base_env)
    env_map["LOOM_RUST_BRIDGE"] = "1"
    env_map["LOOM_RUST_PROFILE"] = settings.profile
    env_map["LOOM_RUST_PORT"] = str(settings.rust_port)
    env_map["LOOM_RUST_DATA_DIR"] = str(settings.data_dir)
    env_map["LOOM_RUST_BRIDGE_PORT"] = str(settings.bridge_port)
    env_map["LOOM_RUST_BRIDGE_URL"] = settings.bridge_url
    env_map["LOOM_TERMINAL_BRIDGE_URL"] = settings.bridge_url
    env_map["LOOM_RUST_BRIDGE_MODE"] = settings.launch_mode
    env_map["LOOM_TERMINAL_BACKEND"] = "iterm2"

    # Rust currently resolves its persistent data dir from LOOM_INSTALL_DIR.
    env_map["LOOM_INSTALL_DIR"] = str(settings.data_dir)

    # Make the Rust child runtime self-describing.
    env_map["LOOM_PROFILE"] = settings.profile
    env_map["LOOM_PORT"] = str(settings.rust_port)
    env_map["LOOM_DATA_DIR"] = str(settings.data_dir)
    return env_map



def _probe_runtime_payload(
    port: int,
    *,
    timeout: float = PROBE_TIMEOUT_SECS,
    urlopen=urllib.request.urlopen,
) -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/terminal-bridge/runtime",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        payload = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None

    runtime = payload.get("runtime")
    return runtime if isinstance(runtime, dict) else None



def _is_port_open(port: int, *, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RustBridgeCallbackClient:
    """Posts iTerm2-originated updates back into the Rust daemon."""

    def __init__(self, rust_url: str):
        self._rust_url = rust_url.rstrip("/") + "/"
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def post_json(self, path: str, payload: dict[str, Any]) -> None:
        await self.start()
        assert self._session is not None
        url = self._rust_url.rstrip("/") + "/" + path.lstrip("/")
        async with self._session.post(url, json=payload) as response:
            if response.status >= 400:
                body = await response.text()
                raise RustBridgeError(
                    f"Rust bridge callback {path} failed with {response.status}: {body}"
                )

    async def post_focus_update(self, *, active_session_id: str = "", current_window_id: str = "") -> None:
        await self.post_json(
            "/api/terminal-bridge/focus",
            {
                "active_session_id": active_session_id,
                "current_window_id": current_window_id,
            },
        )

    async def post_agent_sync(self, cell_payload: dict[str, Any]) -> None:
        await self.post_json(
            "/api/terminal-bridge/agent-sync",
            {"cell": cell_payload},
        )


class RustBridgeCallbackState(MatrixState):
    """MatrixState variant that forwards terminal-originated deltas to Rust."""

    def __init__(self, callback_client: RustBridgeCallbackClient):
        super().__init__(db=None)
        self._callback_client = callback_client

    async def broadcast(self):
        if not self._delta_ops:
            return
        ops = list(self._delta_ops)
        self._delta_ops = []
        self._seq += 1

        focus_payload: dict[str, Any] | None = None
        agent_payloads: dict[str, dict[str, Any]] = {}
        for op in ops:
            kind = (op or {}).get("op")
            if kind == "focus_update":
                focus_payload = {
                    "active_session_id": op.get("active_session_id") or "",
                    "current_window_id": op.get("current_window_id") or "",
                }
            elif kind == "agent_upsert":
                cell_id = str(op.get("id") or "")
                if cell_id:
                    agent_payloads[cell_id] = _terminal_sync_payload(op)

        if focus_payload:
            try:
                await self._callback_client.post_focus_update(**focus_payload)
            except Exception:
                log.exception("Failed to forward focus update to Rust")
        for payload in agent_payloads.values():
            try:
                await self._callback_client.post_agent_sync(payload)
            except Exception:
                log.exception("Failed to forward agent sync to Rust for %s", payload.get("id", ""))


class RustBridgeService:
    """Local HTTP control service that Rust uses for iTerm2-only operations."""

    def __init__(
        self,
        connection,
        *,
        settings: RustBridgeSettings,
        callback_client: RustBridgeCallbackClient,
    ):
        self.settings = settings
        self.state = RustBridgeCallbackState(callback_client)
        self.bridge = ITerm2BridgeCore(connection, self.state)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._app = web.Application()
        self._app.add_routes([
            web.get("/bridge/ping", self.handle_ping),
            web.post("/bridge/list_profiles", self.handle_list_profiles),
            web.post("/bridge/get_launch_context", self.handle_get_launch_context),
            web.post("/bridge/create_session", self.handle_create_session),
            web.post("/bridge/update_session", self.handle_update_session),
            web.post("/bridge/close_session", self.handle_close_session),
            web.post("/bridge/focus_session", self.handle_focus_session),
            web.post("/bridge/send_text", self.handle_send_text),
            web.post("/bridge/write_input", self.handle_write_input),
            web.post("/bridge/signal_input_ready", self.handle_signal_input_ready),
        ])

    async def start(self) -> None:
        await self.bridge.start()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.settings.bridge_port)
        await self._site.start()
        log.info("Rust bridge service listening on %s", self.settings.bridge_url)

    async def stop(self) -> None:
        await self.bridge.shutdown()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def handle_ping(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "bridge_url": self.settings.bridge_url})

    async def handle_list_profiles(self, _request: web.Request) -> web.Response:
        profiles = await self.bridge.list_profiles()
        return web.json_response({"ok": True, "profiles": profiles})

    async def handle_get_launch_context(self, _request: web.Request) -> web.Response:
        ctx = await self.bridge.get_launch_context()
        return web.json_response({"ok": True, "launch_context": asdict(ctx)})

    async def handle_create_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._upsert_cell(payload.get("cell") or payload)
        await self.bridge.create_session(
            cell,
            env_vars=payload.get("env_vars"),
            env_file=payload.get("env_file", ""),
            init_script=payload.get("init_script", ""),
            shell=payload.get("shell", ""),
            system_prompt=payload.get("system_prompt", ""),
            target_session_id=payload.get("target_session_id", ""),
            target_window_id=payload.get("target_window_id", ""),
            restore_focus_to_prev_tab=bool(payload.get("restore_focus_to_prev_tab", False)),
        )
        return web.json_response({"ok": True, "cell": asdict(cell)})

    async def handle_update_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._upsert_cell(payload.get("cell") or payload)
        await self.bridge.update_session(cell, old_name=payload.get("old_name", ""))
        return web.json_response({"ok": True, "cell": asdict(cell)})

    async def handle_close_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._lookup_cell(payload)
        session_id = self._resolve_session_id(payload, cell)
        if session_id:
            await self.bridge.close_session(session_id)
        if cell:
            cell.session_id = None
            cell.status = "stopped"
        return web.json_response({"ok": True})

    async def handle_focus_session(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._lookup_cell(payload)
        session_id = self._resolve_session_id(payload, cell)
        if not session_id:
            raise web.HTTPBadRequest(text="missing session_id or cell_id")
        focused = await self.bridge.focus_session(session_id)
        return web.json_response({"ok": True, "focused": focused})

    async def handle_send_text(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._lookup_cell(payload)
        session_id = self._resolve_session_id(payload, cell)
        if not session_id:
            raise web.HTTPBadRequest(text="missing session_id or cell_id")
        await self.bridge.send_text(
            session_id,
            payload.get("text", ""),
            settled_submit=bool(payload.get("settled_submit", False)),
        )
        return web.json_response({"ok": True})

    async def handle_write_input(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._lookup_cell(payload)
        session_id = self._resolve_session_id(payload, cell)
        if not session_id:
            raise web.HTTPBadRequest(text="missing session_id or cell_id")
        await self.bridge.write_input(session_id, payload.get("data", ""))
        return web.json_response({"ok": True})

    async def handle_signal_input_ready(self, request: web.Request) -> web.Response:
        payload = await request.json()
        cell = self._lookup_cell(payload)
        if cell:
            self.bridge.signal_input_ready(cell.id)
        session_id = self._resolve_session_id(payload, cell)
        if session_id:
            self.bridge.prime_input_ready(session_id)
        return web.json_response({"ok": True})

    def _deserialize_cell(self, payload: Mapping[str, Any]) -> AgentCell:
        data: dict[str, Any] = {}
        for name in _AGENT_CELL_FIELD_NAMES:
            if name in payload:
                data[name] = payload[name]
        if "session_id" in data and not data["session_id"]:
            data["session_id"] = None
        return AgentCell(**data)

    def _upsert_cell(self, payload: Mapping[str, Any]) -> AgentCell:
        cell_id = str(payload.get("id") or "")
        if not cell_id:
            raise web.HTTPBadRequest(text="missing cell.id")
        existing = self.state.agents.get(cell_id)
        if existing is None:
            cell = self._deserialize_cell(payload)
            self.state.agents[cell.id] = cell
            self.state.groups.setdefault(cell.group, [])
            if cell.id not in self.state.groups[cell.group]:
                self.state.groups[cell.group].append(cell.id)
            self.state.group_settings.setdefault(cell.group, GroupSettings())
            if cell.parent_id:
                self.state._children.setdefault(cell.parent_id, [])
                if cell.id not in self.state._children[cell.parent_id]:
                    self.state._children[cell.parent_id].append(cell.id)
            return cell

        old_group = existing.group
        old_parent = existing.parent_id
        for name in _AGENT_CELL_FIELD_NAMES:
            if name not in payload:
                continue
            value = payload[name]
            if name == "session_id" and not value:
                value = None
            setattr(existing, name, value)

        if old_group != existing.group:
            if old_group in self.state.groups:
                self.state.groups[old_group] = [aid for aid in self.state.groups[old_group] if aid != existing.id]
            self.state.groups.setdefault(existing.group, [])
            if existing.id not in self.state.groups[existing.group]:
                self.state.groups[existing.group].append(existing.id)
        self.state.group_settings.setdefault(existing.group, GroupSettings())

        if old_parent != existing.parent_id:
            if old_parent in self.state._children:
                self.state._children[old_parent] = [aid for aid in self.state._children[old_parent] if aid != existing.id]
            if existing.parent_id:
                self.state._children.setdefault(existing.parent_id, [])
                if existing.id not in self.state._children[existing.parent_id]:
                    self.state._children[existing.parent_id].append(existing.id)
        return existing

    def _lookup_cell(self, payload: Mapping[str, Any]) -> AgentCell | None:
        cell_id = str(payload.get("cell_id") or "")
        if cell_id:
            return self.state.agents.get(cell_id)
        cell_payload = payload.get("cell")
        if isinstance(cell_payload, Mapping):
            cell_id = str(cell_payload.get("id") or "")
            if cell_id:
                return self.state.agents.get(cell_id)
        return None

    def _resolve_session_id(self, payload: Mapping[str, Any], cell: AgentCell | None = None) -> str:
        session_id = str(payload.get("session_id") or "")
        if session_id:
            return session_id
        return (cell.session_id or "") if cell else ""


class RustBridgeLauncher:
    """Launches or attaches to the Rust daemon, then hosts the local bridge service."""

    def __init__(
        self,
        *,
        settings: RustBridgeSettings | None = None,
        env: Mapping[str, str] | None = None,
        script_dir: Path | None = None,
        popen_factory=subprocess.Popen,
        urlopen=urllib.request.urlopen,
        sleep_fn=time.sleep,
        monotonic=time.monotonic,
        browser_open=webbrowser.open,
    ):
        self.settings = settings or resolve_rust_bridge_settings(env=env, script_dir=script_dir)
        self.env = build_rust_server_env(self.settings, base_env=env)
        self._popen_factory = popen_factory
        self._urlopen = urlopen
        self._sleep = sleep_fn
        self._monotonic = monotonic
        self._browser_open = browser_open
        self._rust_process: subprocess.Popen | None = None
        self._attached_to_existing = False
        self._service: RustBridgeService | None = None
        self._callback_client: RustBridgeCallbackClient | None = None

    def _describe_runtime(self, runtime: Mapping[str, Any]) -> str:
        details = []
        profile = str(runtime.get("profile") or "").strip()
        if profile:
            details.append(f"profile={profile}")
        data_dir = str(runtime.get("data_dir") or "").strip()
        if data_dir:
            details.append(f"data_dir={data_dir}")
        bridge_url = str(runtime.get("bridge_url") or "").strip()
        if bridge_url:
            details.append(f"bridge_url={bridge_url}")
        return ", ".join(details) if details else "no runtime metadata"

    def _runtime_matches_target(self, runtime: Mapping[str, Any]) -> tuple[bool, list[str]]:
        mismatches: list[str] = []
        saw_identity = False
        runtime_profile = str(runtime.get("profile") or "").strip()
        if runtime_profile:
            saw_identity = True
            if runtime_profile != self.settings.profile:
                mismatches.append(
                    f"profile is '{runtime_profile}' instead of '{self.settings.profile}'"
                )
        runtime_data_dir = str(runtime.get("data_dir") or "").strip()
        if runtime_data_dir:
            saw_identity = True
            actual = Path(runtime_data_dir).expanduser()
            expected = self.settings.data_dir.expanduser()
            try:
                actual = actual.resolve()
            except FileNotFoundError:
                actual = actual.absolute()
            try:
                expected = expected.resolve()
            except FileNotFoundError:
                expected = expected.absolute()
            if actual != expected:
                mismatches.append(f"data dir is '{actual}' instead of '{expected}'")
        if not saw_identity:
            mismatches.append("the runtime did not advertise a profile or data dir")
        return (len(mismatches) == 0), mismatches

    def probe_runtime(self, timeout: float = PROBE_TIMEOUT_SECS) -> dict[str, Any] | None:
        return _probe_runtime_payload(
            self.settings.rust_port,
            timeout=timeout,
            urlopen=self._urlopen,
        )

    def ensure_rust_server(self) -> dict[str, Any]:
        runtime = self.probe_runtime()
        if runtime:
            matches_target, mismatches = self._runtime_matches_target(runtime)
            if self.settings.launch_mode == RUST_BRIDGE_MODE_ATTACH:
                if not matches_target:
                    details = "; ".join(mismatches) or self._describe_runtime(runtime)
                    raise RustBridgeError(
                        "Refusing to attach the Rust bridge runtime because "
                        f"{details}. Choose matching LOOM_RUST_PROFILE/LOOM_RUST_DATA_DIR/LOOM_RUST_PORT values."
                    )
                self._attached_to_existing = True
                log.info("Attaching Rust bridge runtime to %s", self.settings.rust_url)
                return runtime

            if matches_target:
                raise RustBridgeError(
                    "A matching Rust Loom server is already listening on "
                    f"{self.settings.rust_url}. Reuse it with LOOM_RUST_BRIDGE_MODE=attach instead of spawning another."
                )
            raise RustBridgeError(
                f"A different Rust Loom server is already listening on {self.settings.rust_url} "
                f"({self._describe_runtime(runtime)}). Choose a different LOOM_RUST_PORT or attach only to the matching runtime."
            )

        if self.settings.launch_mode == RUST_BRIDGE_MODE_ATTACH:
            raise RustBridgeError(
                f"No Rust Loom server is listening on {self.settings.rust_url} for attach mode."
            )

        if _is_port_open(self.settings.rust_port):
            raise RustBridgeError(
                f"Port {self.settings.rust_port} is already in use by another process. "
                "Set LOOM_RUST_PORT to a free port before launching the bridge runtime."
            )

        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("Launching Rust-owned Loom server on %s", self.settings.rust_url)
        self._rust_process = self._popen_factory(
            shlex.split(self.settings.rust_command),
            cwd=self.settings.rust_cwd,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = self._monotonic() + STARTUP_TIMEOUT_SECS
        while self._monotonic() < deadline:
            runtime = self.probe_runtime(timeout=PROBE_TIMEOUT_SECS)
            if runtime:
                matches_target, mismatches = self._runtime_matches_target(runtime)
                if not matches_target:
                    raise RustBridgeError(
                        "The spawned Rust Loom server came up with the wrong runtime identity: "
                        + "; ".join(mismatches)
                    )
                return runtime
            if self._rust_process is not None and self._rust_process.poll() is not None:
                raise RustBridgeError(
                    f"Rust Loom exited before the bridge runtime became ready (exit code {self._rust_process.returncode})."
                )
            self._sleep(POLL_INTERVAL_SECS)

        raise RustBridgeError(
            f"Timed out waiting for the Rust Loom server on {self.settings.rust_url}."
        )

    async def start_service(self, connection) -> RustBridgeService:
        self._callback_client = RustBridgeCallbackClient(self.settings.rust_url)
        await self._callback_client.start()
        self._service = RustBridgeService(
            connection,
            settings=self.settings,
            callback_client=self._callback_client,
        )
        await self._service.start()
        await self._configure_rust_bridge()
        return self._service

    async def stop_service(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None
        if self._callback_client is not None:
            await self._callback_client.close()
            self._callback_client = None

    async def _configure_rust_bridge(self) -> None:
        if self._callback_client is None:
            return
        await self._callback_client.post_json(
            "/api/terminal-bridge/configure",
            {
                "bridge_url": self.settings.bridge_url,
                "profile": self.settings.profile,
                "data_dir": str(self.settings.data_dir),
            },
        )

    def maybe_open_browser(self) -> None:
        if self.settings.open_browser:
            self._browser_open(self.settings.rust_url)

    def stop_rust_server(self) -> None:
        if self._attached_to_existing:
            return
        process = self._rust_process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        finally:
            self._rust_process = None


async def main(connection=None):
    launcher = RustBridgeLauncher()
    await asyncio.to_thread(launcher.ensure_rust_server)
    try:
        await launcher.start_service(connection)
        launcher.maybe_open_browser()
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise
    finally:
        await launcher.stop_service()
        await asyncio.to_thread(launcher.stop_rust_server)
