"""Focused regression coverage for extracted server runtime builders."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.server_ai_runtime import initialize_ai_runtime
from torque.server_event_ingest_runtime import initialize_event_ingest_runtime


class AIRuntimeBoundaryTests(unittest.TestCase):
    def test_initialization_attaches_services_and_preserves_delta_schedule(self):
        state = SimpleNamespace(
            global_settings=SimpleNamespace(
                ai_enabled=True,
                ai_boot_summary_enabled=True,
            ),
            broadcast=object(),
        )
        observers = []
        state.register_delta_observer = lambda callback, *, ops: observers.append(
            (callback, ops)
        )
        loop = mock.Mock()

        with mock.patch(
            "torque.server_ai_runtime.LocalEmbeddingService"
        ) as embedding_cls, mock.patch(
            "torque.server_ai_runtime.AIIndexService"
        ) as index_cls, mock.patch(
            "torque.server_ai_runtime.AISummaryService"
        ) as summary_cls:
            runtime = initialize_ai_runtime(
                db="db",
                state=state,
                data_dir=Path("/tmp/torque-ai"),
                broadcast_callback=state.broadcast,
                loop=loop,
            )

        self.assertIs(state.ai_embedding_service, runtime.embedding_service)
        self.assertIs(state.ai_index_service, runtime.index_service)
        self.assertIs(state.ai_summary_service, runtime.summary_service)
        embedding_cls.assert_called_once_with(data_dir=Path("/tmp/torque-ai"))
        index_cls.assert_called_once_with(
            db="db",
            state=state,
            embedding_service=runtime.embedding_service,
            data_dir=Path("/tmp/torque-ai"),
            broadcast_callback=state.broadcast,
        )
        summary_cls.assert_called_once_with(db="db", state=state)
        self.assertEqual(loop.call_later.call_count, 2)

        callback, ops = observers.pop()
        self.assertEqual(
            ops,
            {
                "architect_journal_append",
                "journal_append",
                "decision_upsert",
                "decision_remove",
                "task_upsert",
                "task_remove",
                "agent_peer_thread_upsert",
                "agent_peer_thread_remove",
            },
        )
        callback({"op": "journal_append"})
        callback({"op": "task_upsert"})
        self.assertEqual(runtime.index_service.schedule_incremental.call_count, 2)
        runtime.summary_service.schedule_for_delta.assert_called_once_with(
            {"op": "journal_append"}
        )


class EventIngestRuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_configures_and_reconfigures_after_reconnect(self):
        calls = []

        class Client:
            socket_path = "/tmp/event-ingest.sock"

            def __init__(self, *, data_dir):
                self.data_dir = data_dir
                self.on_reconnect = None

            async def connect(self):
                calls.append("connect")

        class Drainer:
            def __init__(self, client, event_bus, state, *, daemon_identity):
                self.args = (client, event_bus, state, daemon_identity)

        async def configure(client, state):
            calls.append(("configure", client, state))

        state = object()
        runtime = await initialize_event_ingest_runtime(
            data_dir=Path("/tmp/torque-events"),
            event_bus="bus",
            state=state,
            daemon_identity="profile",
            configure_client=configure,
            client_factory=Client,
            drainer_factory=Drainer,
        )

        self.assertTrue(runtime.configured[0])
        self.assertEqual(calls[0], "connect")
        self.assertEqual(calls[1][0], "configure")
        self.assertEqual(runtime.drainer.args, (runtime.client, "bus", state, "profile"))

        await runtime.client.on_reconnect({})
        self.assertTrue(runtime.configured[0])
        self.assertEqual([call[0] for call in calls if isinstance(call, tuple)], [
            "configure",
            "configure",
        ])

    async def test_failed_startup_stays_retryable(self):
        class Client:
            socket_path = "/tmp/event-ingest.sock"

            def __init__(self, *, data_dir):
                self.on_reconnect = None

            async def connect(self):
                raise RuntimeError("unavailable")

        configured = []

        async def configure(client, state):
            configured.append((client, state))

        runtime = await initialize_event_ingest_runtime(
            data_dir=Path("/tmp/torque-events"),
            event_bus="bus",
            state="state",
            daemon_identity="profile",
            configure_client=configure,
            client_factory=Client,
            drainer_factory=lambda *args, **kwargs: object(),
        )

        self.assertFalse(runtime.configured[0])
        await runtime.ensure_configured()
        self.assertTrue(runtime.configured[0])
        self.assertEqual(len(configured), 1)


if __name__ == "__main__":
    unittest.main()
