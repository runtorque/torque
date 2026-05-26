import asyncio
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


async def _remote_user_agent_message(_payload):
    return {"type": "ok"}


def _relay_settings(**overrides):
    base = {
        "relay_enabled": False,
        "relay_url": "",
        "relay_daemon_id": "",
        "relay_credential_id": "",
        "relay_private_key_path": "",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


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

    async def test_state_delta_observer_registry_schedules_and_unregisters(self):
        ops = [{"op": "agent_upsert", "id": "agent-1", "last_event_at": 123}]
        self.assertEqual(
            self.cloud_hooks.notify_state_delta_observers(ops, state=object()),
            0,
        )

        events = []

        async def observer(batch):
            events.append(batch)

        unregister = self.cloud_hooks.register_state_delta_observer(observer)
        try:
            self.assertEqual(
                self.cloud_hooks.notify_state_delta_observers(ops, state=object()),
                1,
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            unregister()

        self.assertEqual(events, [ops])
        ops[0]["id"] = "mutated"
        self.assertEqual(events[0][0]["id"], "agent-1")
        self.assertEqual(
            self.cloud_hooks.notify_state_delta_observers(ops, state=object()),
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

    async def test_missing_module_reports_visible_relay_connection_error(self):
        module_name = "missing_ee_for_relay_state_test"
        reports = []

        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True), \
             mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
                config={
                    "enabled": True,
                    "relay_url": "wss://relay.example/v1/daemon/d1/ws",
                    "daemon_id": "d1",
                },
                report_connection_state=reports.append,
            )
            with self.assertLogs(self.cloud_hooks.log, level="DEBUG"):
                runtime = await self.cloud_hooks.start_cloud_connector(context)

        self.assertTrue(runtime.enabled)
        self.assertFalse(runtime.started)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "error")
        self.assertTrue(reports[0]["enabled"])
        self.assertEqual(reports[0]["relay_host"], "relay.example")
        self.assertEqual(reports[0]["daemon_id"], "d1")
        self.assertIn("PYTHONPATH", reports[0]["last_error"])
        self.assertIn("cryptography", reports[0]["last_error"])

    async def test_startup_failure_reports_visible_relay_connection_error(self):
        module_name = "fake_relay_connector_startup_failure"
        module = types.ModuleType(module_name)
        reports = []

        class FakeConnector:
            async def start(self):
                raise ValueError(
                    "cryptography is unavailable; signed relay attach is disabled cleanly"
                )

        module.create_connector = lambda context: FakeConnector()
        sys.modules[module_name] = module
        self.addCleanup(lambda: sys.modules.pop(module_name, None))

        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True), \
             mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
                config={
                    "enabled": True,
                    "relay_url": "https://relay.example",
                    "daemon_id": "daemon-1",
                },
                report_connection_state=reports.append,
            )
            with self.assertLogs(self.cloud_hooks.log, level="ERROR"):
                runtime = await self.cloud_hooks.start_cloud_connector(context)

        self.assertTrue(runtime.enabled)
        self.assertFalse(runtime.started)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["status"], "error")
        self.assertEqual(reports[0]["relay_host"], "relay.example")
        self.assertEqual(reports[0]["daemon_id"], "daemon-1")
        self.assertIn("cryptography is unavailable", reports[0]["last_error"])

    async def test_enabled_connector_imports_factory_and_unregisters_observers_on_stop(self):
        module_name = "fake_torque_ee_connector_for_test"
        module = types.ModuleType(module_name)

        class FakeConnector:
            def __init__(self, context):
                self.context = context
                self.started = False
                self.stopped = False
                self.events = []
                self.delta_batches = []

            async def start(self):
                self.started = True

            async def stop(self):
                self.stopped = True

            def on_direct_message(self, event):
                self.events.append(event)

            async def on_state_delta(self, ops):
                self.delta_batches.append(ops)

        module.create_connector = lambda context: FakeConnector(context)
        sys.modules[module_name] = module
        self.addCleanup(lambda: sys.modules.pop(module_name, None))

        with mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True), \
             mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name):
            context = self.cloud_hooks.CloudConnectorContext(
                state=object(),
                remote_user_agent_message=_remote_user_agent_message,
                register_direct_message_observer=self.cloud_hooks.register_direct_message_observer,
                register_state_delta_observer=self.cloud_hooks.register_state_delta_observer,
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
        self.cloud_hooks.notify_state_delta_observers(
            [{"op": "agent_upsert", "id": "a2"}],
            state=object(),
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(runtime.connector.delta_batches[0][0]["id"], "a2")

        await self.cloud_hooks.stop_cloud_connector(runtime)
        self.assertTrue(runtime.connector.stopped)
        self.assertFalse(runtime.started)
        self.assertEqual(
            self.cloud_hooks.notify_direct_message_observers("direct_message_saved", row),
            0,
        )
        self.assertEqual(
            self.cloud_hooks.notify_state_delta_observers(
                [{"op": "agent_upsert", "id": "a2"}],
                state=object(),
            ),
            0,
        )


class ResolveRelayConfigTests(unittest.TestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(
            importlib.import_module("torque.cloud_hooks")
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name
        # Neutral env baseline so individual tests opt into env fallback.
        self._patches = [
            mock.patch.object(
                self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", False),
            mock.patch.object(
                self.cloud_hooks.torque_config, "CLOUD_RELAY_URL", ""),
            mock.patch.object(
                self.cloud_hooks.torque_config, "CLOUD_DAEMON_ID", ""),
            mock.patch.dict(
                os.environ, {"TORQUE_EE_DAEMON_CREDENTIAL_ID": ""}, clear=False),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_ee_connector(self, payload):
        path = Path(self.data_dir) / "ee_connector.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_settings_are_primary_and_flow_into_context_config(self):
        settings = _relay_settings(
            relay_enabled=True,
            relay_url="wss://relay.example/ws",
            relay_daemon_id="daemon-7",
            relay_credential_id="cred-7",
            relay_private_key_path="/keys/relay.pem",
        )
        resolved = self.cloud_hooks.resolve_relay_config(
            settings, data_dir=self.data_dir)

        self.assertEqual(resolved["config"], {
            "enabled": True,
            "relay_url": "wss://relay.example/ws",
            "daemon_id": "daemon-7",
            "credential_id": "cred-7",
            "private_key_path": "/keys/relay.pem",
        })
        sources = resolved["sources"]
        for field in ("relay_url", "daemon_id", "credential_id",
                      "private_key_path", "enabled"):
            self.assertEqual(sources[field]["source"], "settings", field)
        self.assertEqual(sources["relay_url"]["value"], "wss://relay.example/ws")
        self.assertTrue(sources["enabled"]["value"])

    def test_unset_settings_fall_through_to_file_then_env(self):
        # File supplies relay_url + daemon_id; env supplies credential_id.
        self._write_ee_connector({
            "relay_url": "wss://file.example/ws",
            "daemon_id": "file-daemon",
        })
        with mock.patch.dict(
            os.environ,
            {"TORQUE_EE_DAEMON_CREDENTIAL_ID": "env-cred"},
            clear=False,
        ):
            resolved = self.cloud_hooks.resolve_relay_config(
                _relay_settings(), data_dir=self.data_dir)

        sources = resolved["sources"]
        self.assertEqual(sources["relay_url"]["source"], "ee_connector.json")
        self.assertEqual(sources["relay_url"]["value"], "wss://file.example/ws")
        self.assertEqual(sources["daemon_id"]["source"], "ee_connector.json")
        self.assertEqual(sources["credential_id"]["source"], "env")
        self.assertEqual(sources["credential_id"]["value"], "env-cred")
        # Crucially: no unset field is written into context.config, so the
        # connector's own resolver keeps its file/env fallback intact.
        self.assertEqual(resolved["config"], {"enabled": False})

    def test_settings_override_file_and_env(self):
        self._write_ee_connector({
            "relay_url": "wss://file.example/ws",
            "daemon_id": "file-daemon",
        })
        with mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_RELAY_URL",
            "wss://env.example/ws",
        ):
            resolved = self.cloud_hooks.resolve_relay_config(
                _relay_settings(relay_url="wss://settings.example/ws"),
                data_dir=self.data_dir,
            )
        self.assertEqual(
            resolved["sources"]["relay_url"]["value"],
            "wss://settings.example/ws",
        )
        self.assertEqual(resolved["sources"]["relay_url"]["source"], "settings")
        self.assertEqual(
            resolved["config"]["relay_url"], "wss://settings.example/ws")

    def test_env_enables_when_settings_toggle_off(self):
        with mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True,
        ):
            resolved = self.cloud_hooks.resolve_relay_config(
                _relay_settings(relay_enabled=False), data_dir=self.data_dir)
        self.assertTrue(resolved["config"]["enabled"])
        self.assertEqual(resolved["sources"]["enabled"]["source"], "env")

    def test_inline_pem_in_file_marks_key_file_sourced_without_leaking_pem(self):
        self._write_ee_connector({
            "relay_url": "wss://file.example/ws",
            "private_key_pem": "-----BEGIN PRIVATE KEY-----\nSECRET\n",
        })
        resolved = self.cloud_hooks.resolve_relay_config(
            _relay_settings(), data_dir=self.data_dir)
        key_src = resolved["sources"]["private_key_path"]
        self.assertEqual(key_src["source"], "ee_connector.json")
        # We surface that the key is file-sourced but never the PEM itself.
        self.assertEqual(key_src["value"], "")
        self.assertNotIn("private_key_pem", resolved["config"])
        self.assertNotIn("SECRET", json.dumps(resolved))

    def test_unset_field_resolves_to_empty_unset_source(self):
        resolved = self.cloud_hooks.resolve_relay_config(
            _relay_settings(), data_dir=self.data_dir)
        self.assertEqual(resolved["sources"]["relay_url"]["source"], "")
        self.assertEqual(resolved["sources"]["relay_url"]["value"], "")

    def test_malformed_ee_connector_json_is_non_fatal(self):
        (Path(self.data_dir) / "ee_connector.json").write_text(
            "{not json", encoding="utf-8")
        resolved = self.cloud_hooks.resolve_relay_config(
            _relay_settings(relay_url="wss://settings.example/ws"),
            data_dir=self.data_dir,
        )
        self.assertEqual(
            resolved["sources"]["relay_url"]["value"],
            "wss://settings.example/ws",
        )


class StartConnectorEnableGatingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(
            importlib.import_module("torque.cloud_hooks"))

    def _context(self, config):
        return self.cloud_hooks.CloudConnectorContext(
            state=object(),
            remote_user_agent_message=_remote_user_agent_message,
            register_direct_message_observer=(
                self.cloud_hooks.register_direct_message_observer),
            config=config,
        )

    async def test_settings_enabled_overrides_disabled_env(self):
        module_name = "fake_relay_enable_via_settings"
        module = types.ModuleType(module_name)

        class FakeConnector:
            def __init__(self, context):
                self.started = False

            async def start(self):
                self.started = True

        module.create_connector = lambda ctx: FakeConnector(ctx)
        sys.modules[module_name] = module
        self.addCleanup(lambda: sys.modules.pop(module_name, None))

        with mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", False,
        ), mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_MODULE", module_name,
        ):
            runtime = await self.cloud_hooks.start_cloud_connector(
                self._context({"enabled": True}))
        self.assertTrue(runtime.enabled)
        self.assertTrue(runtime.started)

    async def test_settings_disabled_overrides_enabled_env(self):
        with mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", True,
        ):
            runtime = await self.cloud_hooks.start_cloud_connector(
                self._context({"enabled": False}))
        self.assertFalse(runtime.enabled)
        self.assertFalse(runtime.started)

    async def test_absent_enabled_hint_falls_back_to_env(self):
        with mock.patch.object(
            self.cloud_hooks.torque_config, "CLOUD_CONNECTOR_ENABLED", False,
        ):
            runtime = await self.cloud_hooks.start_cloud_connector(
                self._context({}))
        self.assertFalse(runtime.enabled)


if __name__ == "__main__":
    unittest.main()
