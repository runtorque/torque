"""Discover and reap leaked event-ingest / pty-supervisor sidecar processes.

Both sidecars are spawned detached (``start_new_session=True``) by the daemon
into a ``--data-dir``. When the spawning daemon is killed — e.g. a test or
smoke harness terminating its standalone daemon — the detached sidecars
survive. When that data dir is a temp dir that is later deleted, the sidecars
become orphans pointing at a non-existent directory.

Enough accumulated orphans push the per-uid process/thread count toward the
kernel ceiling (``kern.maxprocperuid`` / ``ulimit -u``); once near the ceiling
a fresh daemon launch's ``asyncio.to_thread`` cannot allocate a worker thread
or lock. That was the root cause of the go-live RLock failure.

This module enumerates running sidecars (via ``ps``) and reaps the orphaned
ones — those whose ``--data-dir`` no longer exists on disk — while always
sparing any sidecar tied to a live data dir (``~/.torque``, ``profiles/*``, or
an explicitly spared dir). A second entry point reaps every sidecar bound to a
specific data dir, for harness teardown that knows the temp dir it spawned
into.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

log = logging.getLogger("torque.sidecar_reaper")

# ``python -m <module>`` token -> short kind label.
SIDECAR_MODULES = {
    "torque.event_ingest_daemon": "event_ingest_daemon",
    "torque.pty_supervisor": "pty_supervisor",
}

_DATA_DIR_RE = re.compile(r"--data-dir(?:=|\s+)(\S+)")


@dataclass(frozen=True)
class Sidecar:
    pid: int
    kind: str
    data_dir: Optional[Path]
    command: str


def _run_ps() -> str:
    """Return ``ps`` output as ``<pid> <command>`` lines (one per process)."""
    try:
        proc = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("sidecar reaper: ps failed: %s", exc)
        return ""
    return proc.stdout or ""


def _extract_data_dir(command: str) -> Optional[Path]:
    """Pull the ``--data-dir`` value out of a process command line."""
    # ``shlex`` honours quoting (paths with spaces); fall back to a regex if
    # the command line is not cleanly tokenizable.
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    for idx, tok in enumerate(tokens):
        if tok == "--data-dir" and idx + 1 < len(tokens):
            return Path(tokens[idx + 1])
        if tok.startswith("--data-dir="):
            return Path(tok.split("=", 1)[1])
    match = _DATA_DIR_RE.search(command)
    if match:
        return Path(match.group(1))
    return None


def discover_sidecars(ps_output: Optional[str] = None) -> list[Sidecar]:
    """Enumerate running event-ingest / pty-supervisor sidecar processes."""
    text = ps_output if ps_output is not None else _run_ps()
    self_pid = os.getpid()
    found: list[Sidecar] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if not head.isdigit():
            continue
        pid = int(head)
        command = rest.strip()
        if pid == self_pid or not command:
            continue
        kind = None
        for module, label in SIDECAR_MODULES.items():
            # Match ``-m torque.<module>`` invocations; require the module
            # token to stand alone so unrelated paths don't false-match.
            if re.search(rf"(?:^|\s)-m\s+{re.escape(module)}(?:\s|$)", command):
                kind = label
                break
        if kind is None:
            continue
        found.append(
            Sidecar(
                pid=pid,
                kind=kind,
                data_dir=_extract_data_dir(command),
                command=command,
            )
        )
    return found


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_pid(pid: int, *, timeout: float = 1.0) -> None:
    """Best-effort terminate of an orphaned (non-child) sidecar pid.

    These processes are detached and reparented to init, so ``os.waitpid`` is
    not available — we poll ``os.kill(pid, 0)`` instead and escalate to
    ``SIGKILL`` if SIGTERM is ignored.
    """
    if not _pid_alive(pid):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    if _pid_alive(pid):
        log.warning("sidecar pid=%s ignored SIGTERM; sending SIGKILL", pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _normalize_dir(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        return os.path.normpath(os.path.expanduser(str(path)))
    except (TypeError, ValueError):
        return str(path)


def find_orphaned_sidecars(
    *,
    spare_data_dirs: Iterable[Path | str] = (),
    ps_output: Optional[str] = None,
    dir_exists: Optional[Callable[[Path], bool]] = None,
) -> list[Sidecar]:
    """Return sidecars whose ``--data-dir`` is missing and not explicitly spared.

    A sidecar is considered orphaned when its data dir no longer exists on
    disk. Any sidecar whose data dir is present (a live daemon's real data dir,
    e.g. ``~/.torque`` or ``profiles/*``) is spared, as is any dir listed in
    ``spare_data_dirs``. Sidecars with an unparseable data dir are left alone.
    """
    exists = dir_exists or (lambda p: Path(p).exists())
    spare = {
        norm
        for norm in (_normalize_dir(Path(d)) for d in spare_data_dirs)
        if norm is not None
    }
    orphans: list[Sidecar] = []
    for sidecar in discover_sidecars(ps_output=ps_output):
        if sidecar.data_dir is None:
            continue
        norm = _normalize_dir(sidecar.data_dir)
        if norm is not None and norm in spare:
            continue
        if exists(sidecar.data_dir):
            continue
        orphans.append(sidecar)
    return orphans


def reap_orphaned_sidecars(
    *,
    spare_data_dirs: Iterable[Path | str] = (),
    dry_run: bool = False,
    ps_output: Optional[str] = None,
    dir_exists: Optional[Callable[[Path], bool]] = None,
    killer: Optional[Callable[[int], None]] = None,
) -> list[Sidecar]:
    """Terminate sidecars whose ``--data-dir`` no longer exists.

    Returns the list of sidecars that were reaped (or, in ``dry_run`` mode,
    that would be reaped). Always spares sidecars bound to a live/present data
    dir or to any dir in ``spare_data_dirs``.
    """
    orphans = find_orphaned_sidecars(
        spare_data_dirs=spare_data_dirs,
        ps_output=ps_output,
        dir_exists=dir_exists,
    )
    kill = killer or _terminate_pid
    reaped: list[Sidecar] = []
    for sidecar in orphans:
        if dry_run:
            reaped.append(sidecar)
            continue
        try:
            kill(sidecar.pid)
        except Exception:
            log.exception(
                "sidecar reaper: failed to terminate %s pid=%s",
                sidecar.kind,
                sidecar.pid,
            )
            continue
        reaped.append(sidecar)
    if reaped and not dry_run:
        log.info(
            "sidecar reaper: terminated %d orphaned sidecar(s): %s",
            len(reaped),
            ", ".join(f"{s.kind}#{s.pid}({s.data_dir})" for s in reaped),
        )
    return reaped


# ProcessPoolExecutor (embedding inference) spawns Python multiprocessing
# helper processes. When the spawning daemon dies uncleanly (SIGKILL, crash,
# power loss — anything that skips graceful ``terminate_workers``), the worker
# blocks forever on its input queue and is reparented to init (PPID 1), each
# pinning the worker's full RSS (GBs, for torch/SentenceTransformer state).
# Unlike the sidecars above there is no ``--data-dir`` to key on, so these are
# identified structurally: a multiprocessing spawn/resource-tracker process
# whose parent is gone.
_MP_WORKER_RE = re.compile(
    r"multiprocessing\.spawn|--multiprocessing-fork|multiprocessing\.resource_tracker"
)


@dataclass(frozen=True)
class OrphanedWorker:
    pid: int
    command: str


def _run_ps_with_ppid() -> str:
    """Return ``ps`` output as ``<pid> <ppid> <command>`` lines."""
    try:
        proc = subprocess.run(
            ["ps", "-axww", "-o", "pid=,ppid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("sidecar reaper: ps (ppid) failed: %s", exc)
        return ""
    return proc.stdout or ""


def discover_orphaned_mp_workers(
    ps_output: Optional[str] = None,
) -> list[OrphanedWorker]:
    """Enumerate leaked multiprocessing workers reparented to init (PPID 1).

    Requiring ``PPID == 1`` is the safety invariant: a ProcessPoolExecutor
    worker with a live parent is in use by some running daemon (this one or a
    co-resident profile) and is never touched. Only a worker whose spawning
    process has already died gets reparented to PID 1, where it blocks forever
    on a queue nobody writes to — safe to kill regardless of which app spawned
    it.
    """
    text = ps_output if ps_output is not None else _run_ps_with_ppid()
    self_pid = os.getpid()
    found: list[OrphanedWorker] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, ppid_s, command = parts
        if not pid_s.isdigit() or not ppid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == self_pid or int(ppid_s) != 1:
            continue
        if not _MP_WORKER_RE.search(command):
            continue
        found.append(OrphanedWorker(pid=pid, command=command))
    return found


def reap_orphaned_mp_workers(
    *,
    dry_run: bool = False,
    ps_output: Optional[str] = None,
    killer: Optional[Callable[[int], None]] = None,
) -> list[OrphanedWorker]:
    """Terminate leaked multiprocessing workers reparented to init.

    Complements ``reap_orphaned_sidecars`` for the embedding
    ``ProcessPoolExecutor`` worker, which has no ``--data-dir`` and only leaks
    on unclean daemon death (the graceful SIGTERM path now reaps it directly).
    """
    orphans = discover_orphaned_mp_workers(ps_output=ps_output)
    kill = killer or _terminate_pid
    reaped: list[OrphanedWorker] = []
    for worker in orphans:
        if dry_run:
            reaped.append(worker)
            continue
        try:
            kill(worker.pid)
        except Exception:
            log.exception(
                "sidecar reaper: failed to terminate mp worker pid=%s",
                worker.pid,
            )
            continue
        reaped.append(worker)
    if reaped and not dry_run:
        log.info(
            "sidecar reaper: terminated %d orphaned multiprocessing worker(s): %s",
            len(reaped),
            ", ".join(str(w.pid) for w in reaped),
        )
    return reaped


def reap_sidecars_for_data_dir(
    data_dir: Path | str,
    *,
    dry_run: bool = False,
    ps_output: Optional[str] = None,
    killer: Optional[Callable[[int], None]] = None,
) -> list[Sidecar]:
    """Terminate every sidecar bound to ``data_dir`` regardless of dir presence.

    Intended for harness teardown: the caller knows the temp data dir it
    spawned into and wants its sidecars gone even while the dir still exists.
    """
    target = _normalize_dir(Path(data_dir))
    kill = killer or _terminate_pid
    reaped: list[Sidecar] = []
    for sidecar in discover_sidecars(ps_output=ps_output):
        if sidecar.data_dir is None:
            continue
        if _normalize_dir(sidecar.data_dir) != target:
            continue
        if dry_run:
            reaped.append(sidecar)
            continue
        try:
            kill(sidecar.pid)
        except Exception:
            log.exception(
                "sidecar reaper: failed to terminate %s pid=%s",
                sidecar.kind,
                sidecar.pid,
            )
            continue
        reaped.append(sidecar)
    if reaped and not dry_run:
        log.info(
            "sidecar reaper: terminated %d sidecar(s) for data dir %s",
            len(reaped),
            target,
        )
    return reaped
