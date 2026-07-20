"""Exclusive ownership of a Torque runtime data directory.

The authoritative Torque database and durable event-ingest cursor are scoped to
``DATA_DIR``, not to the HTTP port.  Holding this lock is therefore a
precondition for starting the main backend runtime.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


OWNER_FILE_NAME = "daemon-owner.lock"
OWNER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProcessInspection:
    """Best-effort liveness and identity evidence for one PID."""

    status: str
    identity: dict
    detail: str = ""


class DaemonOwnershipError(RuntimeError):
    """Base error for profile ownership failures."""


class DaemonAlreadyOwnedError(DaemonOwnershipError):
    """Raised when another main backend holds the resolved data directory."""

    def __init__(self, message: str, *, owner: dict | None = None):
        super().__init__(message)
        self.owner = dict(owner or {})


class UnsafeDaemonOwnershipError(DaemonOwnershipError):
    """Raised when stale ownership cannot be demonstrated safely."""

    def __init__(self, message: str, *, owner: dict | None = None):
        super().__init__(message)
        self.owner = dict(owner or {})


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _read_linux_process_identity(pid: int) -> dict:
    proc_dir = Path("/proc") / str(pid)
    stat_text = (proc_dir / "stat").read_text(encoding="utf-8")
    close_paren = stat_text.rfind(")")
    if close_paren < 0:
        raise RuntimeError(f"unexpected /proc/{pid}/stat format")
    # Fields following ``comm`` begin with field 3. Linux process start time is
    # field 22, hence index 19 in this tail.
    stat_tail = stat_text[close_paren + 2 :].split()
    start_ticks = stat_tail[19]
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        boot_id = ""
    try:
        executable = str((proc_dir / "exe").resolve(strict=True))
    except OSError:
        executable = ""
    try:
        command = (proc_dir / "cmdline").read_bytes().replace(
            b"\0", b" "
        ).decode("utf-8", "replace").strip()
    except OSError:
        command = ""
    return {
        "kind": "linux-proc",
        "start_token": f"{boot_id}:{start_ticks}",
        "executable": executable,
        "command": command,
    }


def _run_ps_field(pid: int, field: str) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", f"{field}="],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    if result.returncode:
        raise ProcessLookupError(pid)
    return result.stdout.strip()


def _read_ps_process_identity(pid: int) -> dict:
    started = _run_ps_field(pid, "lstart")
    if not started:
        raise ProcessLookupError(pid)
    return {
        "kind": "ps",
        "start_token": started,
        "executable": _run_ps_field(pid, "comm"),
        "command": _run_ps_field(pid, "command"),
    }


def inspect_process(pid: int) -> ProcessInspection:
    """Inspect a PID without treating permission or identity errors as death."""

    if pid <= 0:
        return ProcessInspection("unknown", {}, "owner PID is missing or invalid")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ProcessInspection("dead", {}, "process does not exist")
    except PermissionError as exc:
        return ProcessInspection("unknown", {}, f"liveness permission denied: {exc}")
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return ProcessInspection("dead", {}, "process does not exist")
        return ProcessInspection("unknown", {}, f"liveness check failed: {exc}")

    try:
        if Path("/proc").is_dir() and (Path("/proc") / str(pid)).exists():
            identity = _read_linux_process_identity(pid)
        else:
            identity = _read_ps_process_identity(pid)
    except ProcessLookupError:
        # The process may have exited between kill(0) and identity collection.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return ProcessInspection("dead", {}, "process exited during inspection")
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return ProcessInspection(
                    "dead", {}, "process exited during inspection"
                )
        return ProcessInspection(
            "unknown", {}, "process identity disappeared during inspection"
        )
    except Exception as exc:
        return ProcessInspection("unknown", {}, f"identity check failed: {exc}")
    if not identity.get("start_token"):
        return ProcessInspection(
            "unknown", identity, "process start identity is unavailable"
        )
    return ProcessInspection("live", identity)


def _identity_matches(recorded: dict, current: dict) -> bool:
    recorded_start = str(recorded.get("start_token") or "")
    current_start = str(current.get("start_token") or "")
    if not recorded_start or not current_start or recorded_start != current_start:
        return False
    recorded_executable = str(recorded.get("executable") or "")
    current_executable = str(current.get("executable") or "")
    if (
        recorded_executable
        and current_executable
        and _resolved(recorded_executable) != _resolved(current_executable)
    ):
        return False
    return True


def _read_owner(handle) -> tuple[dict | None, str]:
    handle.seek(0)
    raw = handle.read()
    if not raw.strip():
        return None, ""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return None, f"owner metadata is invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "owner metadata is not an object"
    return value, ""


def _owner_summary(owner: dict | None) -> str:
    owner = owner or {}
    identity = dict(owner.get("process_identity") or {})
    fields = [
        f"daemon_id={owner.get('daemon_id') or 'unknown'}",
        f"pid={owner.get('pid') or 'unknown'}",
        f"port={owner.get('port') or 'unknown'}",
        f"profile={owner.get('profile') or 'default'}",
        f"data_dir={owner.get('data_dir') or 'unknown'}",
        f"process_start={identity.get('start_token') or 'unknown'}",
        f"executable={owner.get('executable') or 'unknown'}",
        f"source={owner.get('source_path') or 'unknown'}",
    ]
    return ", ".join(fields)


def _attach_advice(owner: dict | None) -> str:
    port = str((owner or {}).get("port") or "").strip()
    port_hint = f" on port {port}" if port else ""
    return (
        f"Attach to the existing Torque daemon{port_hint}, or choose a "
        "distinct TORQUE_PROFILE/TORQUE_DATA_DIR."
    )


class ProfileDaemonOwner:
    """A held, profile-scoped main-backend ownership lock."""

    def __init__(self, *, handle, path: Path, metadata: dict):
        self._handle = handle
        self.path = path
        self.metadata = metadata
        self._released = False

    @property
    def daemon_id(self) -> str:
        return str(self.metadata.get("daemon_id") or "")

    @property
    def label(self) -> str:
        return (
            f"{self.daemon_id or 'unknown'} "
            f"pid={self.metadata.get('pid')} port={self.metadata.get('port')} "
            f"profile={self.metadata.get('profile')} "
            f"data_dir={self.metadata.get('data_dir')}"
        )

    @classmethod
    def acquire(
        cls,
        *,
        data_dir: Path | str,
        profile: str,
        port: int,
        source_path: Path | str,
        executable: Path | str | None = None,
        process_inspector: Callable[[int], ProcessInspection] = inspect_process,
    ) -> "ProfileDaemonOwner":
        resolved_data_dir = _resolved(data_dir)
        resolved_data_dir.mkdir(parents=True, exist_ok=True)
        owner_path = resolved_data_dir / OWNER_FILE_NAME
        handle = owner_path.open("a+", encoding="utf-8")
        try:
            os.chmod(owner_path, 0o600)
        except OSError:
            pass

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                handle.close()
                raise
            owner, metadata_error = _read_owner(handle)
            handle.close()
            detail = _owner_summary(owner)
            if metadata_error:
                detail = f"{detail}; {metadata_error}"
            raise DaemonAlreadyOwnedError(
                "Torque backend startup refused because this resolved profile "
                f"already has a live owner ({detail}). {_attach_advice(owner)}",
                owner=owner,
            )

        try:
            previous, metadata_error = _read_owner(handle)
            if metadata_error:
                raise UnsafeDaemonOwnershipError(
                    "Torque backend startup refused because prior owner metadata "
                    f"at {owner_path} cannot be verified safely "
                    f"({metadata_error}). Refusing automatic reclamation; "
                    "verify the old daemon is stopped before removing the owner "
                    "file manually. "
                    f"{_attach_advice(previous)}",
                    owner=previous,
                )
            if previous:
                try:
                    previous_pid = int(previous.get("pid") or 0)
                except (TypeError, ValueError):
                    previous_pid = 0
                inspection = process_inspector(previous_pid)
                if inspection.status != "dead":
                    recorded_identity = dict(
                        previous.get("process_identity") or {}
                    )
                    if inspection.status == "live":
                        identity_state = (
                            "matches the recorded owner"
                            if _identity_matches(
                                recorded_identity, inspection.identity
                            )
                            else (
                                "does not match the recorded process identity "
                                "(possible PID reuse)"
                            )
                        )
                    else:
                        identity_state = (
                            inspection.detail or "cannot be inspected safely"
                        )
                    raise UnsafeDaemonOwnershipError(
                        "Torque backend startup refused because the prior owner "
                        "is not demonstrably stale: "
                        f"{_owner_summary(previous)}; PID {previous_pid} is "
                        f"{inspection.status} and {identity_state}. Refusing "
                        "automatic reclamation. "
                        f"{_attach_advice(previous)}",
                        owner=previous,
                    )

            pid = os.getpid()
            self_inspection = process_inspector(pid)
            if self_inspection.status != "live":
                raise DaemonOwnershipError(
                    "Cannot establish a safe Torque daemon process identity "
                    f"for PID {pid}: {self_inspection.detail or self_inspection.status}"
                )
            metadata = {
                "schema_version": OWNER_SCHEMA_VERSION,
                "daemon_id": uuid.uuid4().hex,
                "pid": pid,
                "port": int(port),
                "profile": str(profile or "default"),
                "data_dir": str(resolved_data_dir),
                "acquired_at": time.time(),
                "executable": str(_resolved(executable or sys.executable)),
                "source_path": str(_resolved(source_path)),
                "process_identity": dict(self_inspection.identity),
            }
            handle.seek(0)
            handle.truncate()
            json.dump(metadata, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            return cls(handle=handle, path=owner_path, metadata=metadata)
        except Exception:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            raise

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            # Do not unlink a flock file: unlinking can create two independently
            # lockable inodes during a handoff race. Empty metadata marks an
            # orderly release while the kernel lock remains held.
            handle.seek(0)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self) -> "ProfileDaemonOwner":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()
