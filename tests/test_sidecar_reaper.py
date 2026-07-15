"""Tests for the leaked-sidecar reaper (proc-ceiling root-cause fix)."""

import contextlib
import os
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path

from torque import sidecar_reaper


class DiscoverSidecarsTests(unittest.TestCase):
    def test_parses_pid_kind_and_data_dir(self):
        ps_output = "\n".join([
            "  101 /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /tmp/torque-history-smoke --log-file /tmp/x.log",
            " 2002 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir /tmp/torque-supervisor-smoke",
        ])
        found = sidecar_reaper.discover_sidecars(ps_output=ps_output)
        self.assertEqual(len(found), 2)
        by_kind = {s.kind: s for s in found}
        self.assertEqual(by_kind["event_ingest_daemon"].pid, 101)
        self.assertEqual(
            by_kind["event_ingest_daemon"].data_dir,
            Path("/tmp/torque-history-smoke"),
        )
        self.assertEqual(by_kind["pty_supervisor"].pid, 2002)
        self.assertEqual(
            by_kind["pty_supervisor"].data_dir,
            Path("/tmp/torque-supervisor-smoke"),
        )

    def test_skips_unrelated_and_grep_lines(self):
        ps_output = "\n".join([
            "    1 /sbin/launchd",
            "  333 /usr/bin/python3 -m torque.server",
            "  444 grep -E torque.event_ingest_daemon",
            "  555 /usr/bin/python3 some_event_ingest_daemon_helper.py",
        ])
        self.assertEqual(sidecar_reaper.discover_sidecars(ps_output=ps_output), [])

    def test_handles_equals_form_data_dir(self):
        ps_output = (
            " 777 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir=/var/folders/ab/tmp.XYZ/data"
        )
        found = sidecar_reaper.discover_sidecars(ps_output=ps_output)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].data_dir, Path("/var/folders/ab/tmp.XYZ/data"))

    def test_excludes_current_process(self):
        ps_output = (
            f"{os.getpid()} /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /tmp/whatever"
        )
        self.assertEqual(sidecar_reaper.discover_sidecars(ps_output=ps_output), [])


class FindOrphanedTests(unittest.TestCase):
    def test_spares_present_dir_reaps_missing_dir(self):
        ps_output = "\n".join([
            "  100 /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /live/dir",
            "  200 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir /dead/dir",
        ])
        orphans = sidecar_reaper.find_orphaned_sidecars(
            ps_output=ps_output,
            dir_exists=lambda p: str(p) == "/live/dir",
        )
        self.assertEqual([s.pid for s in orphans], [200])

    def test_spares_explicit_spare_dir_even_if_missing(self):
        ps_output = (
            "  300 /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /home/me/.torque"
        )
        orphans = sidecar_reaper.find_orphaned_sidecars(
            ps_output=ps_output,
            spare_data_dirs=["/home/me/.torque"],
            dir_exists=lambda p: False,
        )
        self.assertEqual(orphans, [])

    def test_ignores_unparseable_data_dir(self):
        ps_output = "  400 /usr/bin/python3 -m torque.pty_supervisor --verbose"
        orphans = sidecar_reaper.find_orphaned_sidecars(
            ps_output=ps_output,
            dir_exists=lambda p: False,
        )
        self.assertEqual(orphans, [])


class ReapTests(unittest.TestCase):
    def test_reap_orphaned_kills_only_orphans(self):
        ps_output = "\n".join([
            "  100 /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /live/dir",
            "  200 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir /dead/dir",
        ])
        killed = []
        reaped = sidecar_reaper.reap_orphaned_sidecars(
            ps_output=ps_output,
            dir_exists=lambda p: str(p) == "/live/dir",
            killer=killed.append,
        )
        self.assertEqual(killed, [200])
        self.assertEqual([s.pid for s in reaped], [200])

    def test_reap_orphaned_dry_run_kills_nothing(self):
        ps_output = (
            "  200 /usr/bin/python3 -m torque.pty_supervisor --data-dir /dead"
        )
        killed = []
        reaped = sidecar_reaper.reap_orphaned_sidecars(
            ps_output=ps_output,
            dir_exists=lambda p: False,
            dry_run=True,
            killer=killed.append,
        )
        self.assertEqual(killed, [])
        self.assertEqual([s.pid for s in reaped], [200])

    def test_reap_for_data_dir_matches_exact_dir(self):
        ps_output = "\n".join([
            "  100 /usr/bin/python3 -m torque.event_ingest_daemon "
            "--data-dir /tmp/mine",
            "  200 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir /tmp/mine",
            "  300 /usr/bin/python3 -m torque.pty_supervisor "
            "--data-dir /tmp/other",
        ])
        killed = []
        reaped = sidecar_reaper.reap_sidecars_for_data_dir(
            "/tmp/mine",
            ps_output=ps_output,
            killer=killed.append,
        )
        self.assertEqual(sorted(killed), [100, 200])
        self.assertEqual(sorted(s.pid for s in reaped), [100, 200])


class EndToEndReapTests(unittest.TestCase):
    """Spawn a real sidecar into a temp dir; assert teardown leaves none."""

    def test_orphaned_sidecar_is_detected_and_reaped(self):
        from torque import pty_supervisor

        tmp = Path(tempfile.mkdtemp(prefix="torque-reaper-smoke-"))
        cleaned = False
        try:
            pid = pty_supervisor.spawn_detached(tmp)
            # Wait until the detached child has actually initialized its
            # listener. A PID can be observable before ``_serve`` binds the
            # socket; deleting the data dir in that window makes the child
            # fail startup and become a zombie that ``kill(pid, 0)`` still
            # reports as alive.
            socket_path = tmp / pty_supervisor.DEFAULT_SOCKET_NAME
            deadline = time.monotonic() + 3.0
            ready = False
            while time.monotonic() < deadline:
                pong = pty_supervisor._ping_socket(socket_path, timeout=0.1)
                if pong and int(pong.get("pid") or 0) == pid:
                    ready = True
                    break
                if not _alive(pid):
                    break
                time.sleep(0.05)
            if not ready:
                self.skipTest("sidecar did not start in this environment")

            # While the data dir exists it is not an orphan.
            present = sidecar_reaper.discover_sidecars()
            self.assertIn(pid, [s.pid for s in present])

            # Delete the data dir → the sidecar becomes a leaked orphan.
            shutil.rmtree(tmp, ignore_errors=True)
            cleaned = True
            orphans = sidecar_reaper.find_orphaned_sidecars()
            # Full-suite runs may have another live Torque daemon or startup
            # reaper racing to clean the just-orphaned sidecar.  If that wins,
            # the end state this smoke cares about is already satisfied.
            if pid not in [s.pid for s in orphans] and not _alive(pid):
                return
            self.assertIn(
                pid,
                [s.pid for s in orphans],
                "deleted-temp-dir sidecar should be flagged orphaned",
            )

            # Reap by exact data dir (harness teardown path) and confirm gone.
            sidecar_reaper.reap_sidecars_for_data_dir(tmp)
            self.assertTrue(
                _wait_dead(pid),
                "leaked sidecar should be terminated on teardown",
            )
        finally:
            if not cleaned:
                shutil.rmtree(tmp, ignore_errors=True)
            with contextlib.suppress(OSError, NameError):
                os.kill(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_dead(pid: int, timeout: float = 3.0) -> bool:
    """Poll for death, reaping any zombie (the test process is the parent)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    unittest.main()
