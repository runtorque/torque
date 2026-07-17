"""Best-effort discovery of provider model catalogs.

Codex exposes the account-aware model picker through its app-server
``model/list`` request.  Torque consumes that protocol in a short-lived
subprocess and falls back to the CLI's raw debug catalog for older builds.
Discovery is optional: callers must retain their static provider defaults when
the executable is missing, the protocol changes, or the request times out.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


CODEX_CATALOG_TIMEOUT_SECONDS = 5.0


def discover_codex_models(
    *,
    executable: str | None = None,
    timeout: float = CODEX_CATALOG_TIMEOUT_SECONDS,
) -> list[dict]:
    """Return the visible Codex model catalog, or an empty list on failure."""
    command = _resolve_codex_executable(executable)
    if not command:
        return []

    try:
        payload = _codex_app_server_model_list(
            command,
            timeout=min(max(0.1, timeout), 2.0),
        )
        models = _normalize_codex_models(payload, source="app-server")
        if models:
            return models
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        pass

    try:
        proc = subprocess.run(
            [command, "debug", "models"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=max(0.1, timeout),
            check=False,
            env=os.environ.copy(),
        )
        if proc.returncode != 0:
            return []
        payload = json.loads(proc.stdout or "{}")
        return _normalize_codex_models(payload, source="debug")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return []


def _resolve_codex_executable(executable: str | None = None) -> str:
    if executable:
        return str(executable)
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    candidates = [
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
        Path.home() / ".cargo" / "bin" / "codex",
        Path.home() / ".local" / "bin" / "codex",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _codex_app_server_model_list(command: str, *, timeout: float) -> dict:
    proc = subprocess.Popen(
        [command, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )
    try:
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("Codex app-server stdio was unavailable")

        _write_request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "torque", "version": "1"},
                    "capabilities": {},
                },
            },
        )
        deadline = time.monotonic() + max(0.1, timeout)
        initialized = _read_response(proc, 1, deadline)
        if initialized.get("error"):
            raise RuntimeError("Codex app-server initialization failed")

        _write_request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "model/list",
                "params": {"includeHidden": False},
            },
        )
        response = _read_response(proc, 2, deadline)
        if response.get("error"):
            raise RuntimeError("Codex model/list failed")
        result = response.get("result")
        return result if isinstance(result, dict) else {}
    finally:
        _stop_process(proc)


def _write_request(proc: subprocess.Popen, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _read_response(
    proc: subprocess.Popen,
    request_id: int,
    deadline: float,
) -> dict:
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        remaining = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([proc.stdout], [], [], remaining)
        if not readable:
            break
        line = proc.stdout.readline()
        if not line:
            break
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload
    raise subprocess.TimeoutExpired(proc.args, max(0.0, deadline - time.monotonic()))


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass


def _normalize_codex_models(payload: Any, *, source: str) -> list[dict]:
    raw_models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_models, list) and isinstance(payload, dict):
        raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []

    normalized: list[dict] = []
    for index, raw in enumerate(raw_models):
        if not isinstance(raw, dict):
            continue
        hidden = bool(raw.get("hidden"))
        if source == "debug":
            hidden = str(raw.get("visibility") or "list") != "list"
        if hidden:
            continue
        model_id = str(
            raw.get("model")
            or raw.get("id")
            or raw.get("slug")
            or ""
        ).strip()
        if not model_id:
            continue
        efforts = _normalize_reasoning_efforts(
            raw.get("supportedReasoningEfforts")
            if source == "app-server"
            else raw.get("supported_reasoning_levels")
        )
        normalized.append({
            "id": model_id,
            "display_name": str(
                raw.get("displayName")
                or raw.get("display_name")
                or model_id
            ).strip(),
            "description": str(raw.get("description") or "").strip(),
            "is_default": bool(raw.get("isDefault")),
            "default_reasoning_effort": str(
                raw.get("defaultReasoningEffort")
                or raw.get("default_reasoning_level")
                or ""
            ).strip(),
            "reasoning_efforts": efforts,
            "_order": int(raw.get("priority") or index),
        })

    normalized.sort(
        key=lambda model: (
            not bool(model.get("is_default")),
            int(model.get("_order") or 0),
            str(model.get("display_name") or "").lower(),
        )
    )
    for model in normalized:
        model.pop("_order", None)
    return normalized


def _normalize_reasoning_efforts(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            value = str(
                item.get("reasoningEffort")
                or item.get("effort")
                or item.get("value")
                or ""
            ).strip()
            description = str(item.get("description") or "").strip()
        else:
            value = str(item or "").strip()
            description = ""
        if not value or value in seen:
            continue
        seen.add(value)
        out.append({"value": value, "description": description})
    return out
