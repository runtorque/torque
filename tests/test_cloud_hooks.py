import importlib
import sys
import types
import unittest
from unittest import mock


async def _remote_user_agent_message(_payload):
    return {"type": "ok"}


class CloudHooksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(importlib.import_module("torque.cloud_hooks"))

    async def test_connector_lifecycle_is_noop_by_default(self):
        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", False):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
            )
            runtime = await self.cloud_hooks.start_cloud_connector(context)

        self.assertFalse(runtime.enabled)
        self.assertFalse(runtime.started)
        self.assertEqual(runtime.module_name, "torque_ee_connector")

    def test_direct_message_observer_registry_is_noop_until_registered(self):
        row = {"id": "msg-1", "sender_kind": "agent", "sender_id": "a1", "recipient_kind": "user"}
        self.assertEqual(
            self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row),
            0,
        )

        events = []
        unregister = self.cloud_hooks.register_direct_message_observer(events.append)
        try:
            self.assertEqual(
                self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row),
                1,
            )
        finally:
            unregister()

        self.assertEqual(events[0]["type"], "direct_message_saved")
        self.assertEqual(events[0]["row"]["id"], "msg-1")
        self.assertEqual(events[0]["agent_ids"], ["a1"])
        self.assertEqual(
            self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row),
            0,
        )

    async def test_enabled_but_missing_module_fails_clean_with_warning_not_traceback(self):
        # A routine config mistake (e.g. forgot PYTHONPATH=<repo>/ee/python) must
        # not read like an unhandled crash: no raise, fail-clean runtime, and a
        # single actionable WARNING rather than an ERROR-with-traceback.
        module_name = "definitely_missing_torque_ee_connector_for_test"
        self.assertNotIn(module_name, sys.modules)

        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True), \
             mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
            )
            with self.assertLogs(self.cloud_hooks.log, level="DEBUG") as captured:
                runtime = await self.cloud_hooks.start_cloud_connector(context)

        self.assertTrue(runtime.enabled)
        self.assertFalse(runtime.started)
        self.assertTrue(runtime.error)

        warnings = [r for r in captured.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertIn("PYTHONPATH", warning.getMessage())
        self.assertIn("WITHOUT the connector", warning.getMessage())
        # Fail-clean config case: no ERROR, and the WARNING carries no traceback.
        self.assertFalse([r for r in captured.records if r.levelname == "ERROR"])
        self.assertIsNone(warning.exc_info)

    async def test_enabled_connector_imports_factory_and_unregisters_observer_on_stop(self):
        module_name = "fake_torque_ee_connector_for_test"
        module = types.ModuleType(module_name)

        class FakeConnector:
            def __init__(self, context):
                self.context = context
                self.started = False
                self.stopped = False
                self.events = []

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

            def on_direct_message(self, event):
                self.events.append(event)

        module.create_connector = lambda context: FakeConnector(context)
        sys.modules[module_name] = module
        self.addCleanup(lambda: sys.modules.pop(module_name, None))

        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True), \
             mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
            )
            with self.assertLogs(self.cloud_hooks.log, level="INFO") as captured:
                runtime = await self.cloud_hooks.start_cloud_connector(context)

        self.assertTrue(runtime.enabled)
        self.assertTrue(runtime.started)
        self.assertTrue(runtime.connector.started)
        self.assertTrue(
            any(
                "Cloud connector started from" in r.getMessage()
                for r in captured.records
            )
        )

        row = {"id": "msg-2", "sender_kind": "agent", "sender_id": "a2", "recipient_kind": "user"}
        self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row)
        self.assertEqual(runtime.connector.events[0]["row"]["id"], "msg-2")

        await self.cloud_hooks.stop_cloud_connector(runtime)
        self.assertTrue(runtime.connector.stopped)
        self.assertFalse(runtime.started)
        self.assertEqual(
            self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row),
            0,
        )


if __name__ == "__main__":
    unittest.main()
