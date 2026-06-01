import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class ServerSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        from torque.state import MatrixState
        from torque import server_supervisor

        self.MatrixState = MatrixState
        self.server_supervisor = server_supervisor

    def _runtime(self, **_kwargs):
        return {
            "mode": "standalone",
            "terminal_backend": "pty",
        }

    async def test_payload_available_enriches_owner_and_protocol_limits(self):
        state = self.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Worker A",
            group="Torque",
            command="codex",
            terminal_backend="pty",
        )
        cell.id = "cell-1"
        state.agents = {"cell-1": cell}
        cell.session_id = "sess-1"
        cell.current_path = "/repo/worktree"
        cell.kind = "worker"
        cell.status = "running"

        class Bridge:
            async def list_supervisor_state(self):
                return {
                    "supervisor": {"pid": 999, "started_at": 1778343000.0},
                    "sessions": [{
                        "session_id": "sess-1",
                        "cell_id": "cell-1",
                        "pid": 123,
                        "alive": True,
                        "cols": 120,
                        "rows": 32,
                        "total_bytes": 1234567,
                        "cwd": "/repo",
                        "shell_argv": ["/bin/zsh", "-il"],
                        "bootstrap_dir": "/tmp/bootstrap",
                        "started_at": 1778343600.0,
                    }],
                }

        payload = await self.server_supervisor.build_supervisor_sessions_payload(
            Bridge(), state, self._runtime)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["mode"], "standalone")
        self.assertEqual(payload["terminal_backend"], "pty")
        self.assertEqual(payload["missing_fields"], [
            "exit_status", "input_bytes",
        ])
        supervisor = payload["sessions"][0]
        self.assertEqual(supervisor["row_type"], "supervisor")
        self.assertEqual(supervisor["pid"], 999)
        self.assertEqual(supervisor["started_at"], 1778343000.0)
        self.assertFalse(supervisor["terminable"])
        row = payload["sessions"][1]
        self.assertEqual(row["display_command"], "codex")
        self.assertEqual(row["current_path"], "/repo/worktree")
        self.assertFalse(row["orphan"])
        self.assertEqual(row["owner"]["name"], "Worker A")
        self.assertEqual(row["owner"]["kind"], "worker")
        self.assertEqual(row["started_at"], 1778343600.0)
        self.assertNotIn("started_at", row["missing_fields"])
        self.assertTrue(row["terminable"])

    async def test_orphan_when_owner_missing_or_session_mismatches(self):
        state = self.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(name="Stale", group="Torque", command="claude")
        cell.id = "stale-cell"
        cell.session_id = "different-session"
        state.agents = {"stale-cell": cell}

        missing = self.server_supervisor.normalize_supervisor_session({
            "session_id": "missing-session",
            "cell_id": "missing-cell",
            "shell_argv": ["/bin/zsh", "-il"],
            "cwd": "/repo",
        }, state)
        stale = self.server_supervisor.normalize_supervisor_session({
            "session_id": "stale-session",
            "cell_id": "stale-cell",
            "shell_argv": ["/bin/bash", "-i"],
            "cwd": "/repo2",
        }, state)

        self.assertTrue(missing["orphan"])
        self.assertIsNone(missing["owner"])
        self.assertEqual(missing["display_command"], "/bin/zsh -il")
        self.assertTrue(stale["orphan"])
        self.assertEqual(stale["owner"]["name"], "Stale")
        self.assertEqual(stale["display_command"], "claude")

    async def test_non_supervised_bridge_returns_non_error_unavailable(self):
        payload = await self.server_supervisor.build_supervisor_sessions_payload(
            object(), self.MatrixState(), lambda **_: {
                "mode": "standalone",
                "terminal_backend": "pty",
            })

        self.assertEqual(payload["type"], "supervisor_sessions")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["mode"], "standalone")
        self.assertEqual(payload["terminal_backend"], "pty")
        self.assertEqual(payload["sessions"], [])
        self.assertIn("only available", payload["message"])

    async def test_supervisor_exceptions_return_unavailable_payload(self):
        class Bridge:
            async def list_supervisor_sessions(self):
                raise RuntimeError("socket exploded")

        payload = await self.server_supervisor.build_supervisor_sessions_payload(
            Bridge(), self.MatrixState(), self._runtime)

        self.assertEqual(payload["type"], "supervisor_sessions")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["sessions"], [])
        self.assertIn("failed", payload["message"])
        self.assertEqual(payload["error"], "socket exploded")

    async def test_terminate_payload_delegates_and_refreshes(self):
        state = self.MatrixState()
        calls = []

        class Bridge:
            async def terminate_supervisor_session(self, session_id):
                calls.append(("terminate", session_id))

            async def list_supervisor_state(self):
                calls.append(("list", None))
                return {
                    "supervisor": {"pid": 999, "started_at": 1778343000.0},
                    "sessions": [],
                }

        payload = await self.server_supervisor.build_supervisor_terminate_payload(
            Bridge(), state, self._runtime, "sess-gone")

        self.assertTrue(payload["available"])
        self.assertEqual(payload["terminate_session_id"], "sess-gone")
        self.assertEqual(payload["terminated_session_id"], "sess-gone")
        self.assertEqual(calls, [("terminate", "sess-gone"), ("list", None)])
        self.assertEqual(payload["sessions"][0]["row_type"], "supervisor")

    async def test_health_projection_reports_required_fields_and_states(self):
        class Bridge:
            def __init__(self):
                self.connected = True
                self.watchdog = {}

            def supervisor_connected(self):
                return self.connected

            def supervisor_pid(self):
                return 1234

            def supervisor_started_at(self):
                return 100.0

            def supervisor_last_latency_ms(self):
                return 12.34

            def supervisor_last_reconnect_at(self):
                return 150.0

            def supervisor_reconnect_count(self):
                return 2

            def supervisor_session_count(self):
                return 4

            def supervisor_last_successful_op_at(self):
                return 190.0

            def supervisor_metrics_snapshot(self):
                return {"sessions_current": 3, "ops_total": {"ping": 1}}

            def supervisor_watchdog_status(self):
                return self.watchdog

        bridge = Bridge()
        projection = self.server_supervisor.build_supervisor_health_projection(
            bridge, now=200.0)

        self.assertEqual(projection["state"], "up")
        self.assertEqual(projection["supervisor_pid"], 1234)
        self.assertEqual(projection["uptime"], 100.0)
        self.assertTrue(projection["connected"])
        self.assertEqual(projection["last_op_latency_ms"], 12.3)
        self.assertEqual(projection["last_reconnect_at"], 150.0)
        self.assertEqual(projection["reconnect_count"], 2)
        self.assertEqual(projection["session_count"], 3)
        self.assertEqual(projection["time_since_last_successful_op"], 10.0)

        bridge.connected = False
        projection = self.server_supervisor.build_supervisor_health_projection(
            bridge, now=200.0)
        self.assertEqual(projection["state"], "degraded")

        bridge.watchdog = {"state": "down", "circuit_open": True}
        projection = self.server_supervisor.build_supervisor_health_projection(
            bridge, now=200.0)
        self.assertEqual(projection["state"], "down")

        na = self.server_supervisor.build_supervisor_health_projection(
            object(), profile_skip_pty=True)
        self.assertEqual(na["state"], "na_profile")
        unavailable = self.server_supervisor.build_supervisor_health_projection(
            object())
        self.assertEqual(unavailable["state"], "unavailable")

    async def test_watchdog_bounds_respawn_failures_and_marks_sessions_lost(self):
        events = []
        publishes = []
        attempts = []
        clock = {"mono": 0.0, "wall": 1000.0}

        class Bridge:
            def __init__(self):
                self.status = {}
                self.lost = 0

            def supervisor_connected(self):
                return False

            def supervisor_pid(self):
                return 424242

            def supervisor_reconnect_failures(self):
                return 99

            def set_supervisor_watchdog_status(self, status):
                self.status = dict(status or {})

            async def mark_supervisor_lost(self, *, reason=""):
                if reason == "supervisor_lost" and not self.lost:
                    self.lost = 2
                    return 2
                return 0

        bridge = Bridge()

        def ensure_running(_data_dir):
            attempts.append(clock["mono"])
            raise RuntimeError("spawn failed")

        async def emit_event(kind, detail):
            events.append((kind, dict(detail or {})))

        async def publish():
            publishes.append(dict(bridge.status))

        watchdog = self.server_supervisor.SupervisorLivenessWatchdog(
            bridge=bridge,
            data_dir="/tmp/fake",
            ensure_running=ensure_running,
            pid_alive=lambda _pid: False,
            publish_state=publish,
            emit_event=emit_event,
            max_retries=3,
            retry_window_seconds=60,
            base_backoff_seconds=1,
            max_backoff_seconds=10,
            time_func=lambda: clock["wall"],
            monotonic_func=lambda: clock["mono"],
        )

        await watchdog.check_once()
        clock["mono"] += 1.1
        clock["wall"] += 1.1
        await watchdog.check_once()
        clock["mono"] += 2.1
        clock["wall"] += 2.1
        await watchdog.check_once()

        self.assertEqual(len(attempts), 3)
        self.assertEqual(bridge.lost, 2)
        self.assertEqual(bridge.status["state"], "down")
        self.assertTrue(bridge.status["circuit_open"])
        self.assertIn("supervisor_lost", [kind for kind, _ in events])
        self.assertIn("down", [kind for kind, _ in events])

        # Circuit breaker is open: further ticks must not storm respawns.
        for _ in range(5):
            clock["mono"] += 100
            clock["wall"] += 100
            await watchdog.check_once()
        self.assertEqual(len(attempts), 3)


if __name__ == "__main__":
    unittest.main()
