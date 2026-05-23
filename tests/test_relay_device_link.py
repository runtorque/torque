"""Tests for the b2 daemon-mediated relay device-link mint.

Covers the cloud_hooks delegation helper (mint_relay_device_link) and the
server command's local-confirmation gate (generate_relay_device_link), which
together replace the manual `wrangler d1` OTC seed with a daemon-mediated mint
over the authenticated relay WS.
"""

import importlib
import types
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MintRelayDeviceLinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(importlib.import_module("torque.cloud_hooks"))

    def _runtime(self, **over):
        base = dict(enabled=True, module_name="torque_ee_connector", connector=None, started=True, error="")
        base.update(over)
        return self.cloud_hooks.CloudConnectorRuntime(**base)

    async def test_none_runtime_reports_relay_disabled(self):
        result = await self.cloud_hooks.mint_relay_device_link(None)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "relay_disabled")

    async def test_disabled_runtime_reports_relay_disabled(self):
        result = await self.cloud_hooks.mint_relay_device_link(self._runtime(enabled=False))
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "relay_disabled")

    async def test_not_started_runtime_reports_not_started_with_error_detail(self):
        runtime = self._runtime(started=False, connector=object(), error="boom")
        result = await self.cloud_hooks.mint_relay_device_link(runtime)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "relay_not_started")
        self.assertEqual(result["message"], "boom")

    async def test_connector_without_mint_method_reports_unsupported(self):
        runtime = self._runtime(connector=object())
        result = await self.cloud_hooks.mint_relay_device_link(runtime)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "mint_unsupported")

    async def test_delegates_to_connector_and_passes_label_through(self):
        captured = {}

        class FakeConnector:
            async def mint_client_establish_code(self, *, label=""):
                captured["label"] = label
                return {
                    "ok": True,
                    "code": "RAWCODE-xyz",
                    "establish_url": "https://relay.runtorque.com/establish?code=RAWCODE-xyz",
                    "expires_at": "2026-05-23T00:15:00.000Z",
                    "owner_user_id": "owner-1",
                }

        runtime = self._runtime(connector=FakeConnector())
        result = await self.cloud_hooks.mint_relay_device_link(runtime, label="iphone")
        self.assertEqual(captured["label"], "iphone")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["code"], "RAWCODE-xyz")
        self.assertEqual(result["establish_url"], "https://relay.runtorque.com/establish?code=RAWCODE-xyz")

    async def test_connector_mint_exception_is_caught_and_reported(self):
        class ExplodingConnector:
            async def mint_client_establish_code(self, *, label=""):
                raise RuntimeError("kaboom")

        runtime = self._runtime(connector=ExplodingConnector())
        result = await self.cloud_hooks.mint_relay_device_link(runtime)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "mint_failed")
        self.assertIn("kaboom", result["message"])


class GenerateRelayDeviceLinkCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(importlib.import_module("torque.state"))
        self.server_mod = importlib.reload(importlib.import_module("torque.server"))

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, **freevar_overrides):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code)) and const.co_name == "handle_command"
        )
        closure_values = {name: None for name in handle_code.co_freevars}
        closure_values.update(freevar_overrides)
        closure = tuple(
            self._make_cell(closure_values[name]) for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code, self.server_mod.__dict__, "handle_command", None, closure,
        )

    async def test_missing_confirm_returns_confirmation_required_without_minting(self):
        # No connector runtime is needed: the gate must short-circuit BEFORE any
        # mint attempt when confirm is absent (invariant 3 — local confirmation).
        handle_command = self._extract_handle_command()
        result = await handle_command({"cmd": "generate_relay_device_link"})
        self.assertEqual(result["type"], "relay_device_link")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["status"], "confirmation_required")

    async def test_confirm_true_routes_to_mint_path(self):
        # With confirm=true and no connector wired, the gate proceeds to the mint
        # path, which reports relay_disabled — proving confirm unlocks the mint.
        handle_command = self._extract_handle_command(cloud_connector_runtime_holder=[None])
        result = await handle_command({"cmd": "generate_relay_device_link", "confirm": True})
        self.assertEqual(result["type"], "relay_device_link")
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["error"], "relay_disabled")


if __name__ == "__main__":
    unittest.main()
