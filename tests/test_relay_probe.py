"""Unit tests for the daemon-side relay test-connectivity probe.

Covers the structured outcome mapping (ok / reachable_unauthed / ca_missing /
unreachable / auth_rejected / disabled / misconfigured) with mocked HTTP/WS and
the real connector SSL-verify classifier. The probe RIDES the connector's
certifi-backed context, so the CA-missing path is exercised independently of any
ambient SSL_CERT_FILE.
"""

import importlib
import os
import ssl
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# The probe lazily imports the EE connector helpers; tests add ee/python to the
# path so the real SSL-verify classifier is exercised in the ca_missing case.
EE_PYTHON = Path(__file__).resolve().parents[1] / "ee" / "python"
if str(EE_PYTHON) not in sys.path:
    sys.path.insert(0, str(EE_PYTHON))

from torque_ee_connector.connector import _is_tls_verify_error  # noqa: E402


def _settings(**overrides):
    base = {
        "relay_enabled": True,
        "relay_url": "wss://relay.example/v1/daemon/d1/ws",
        "relay_daemon_id": "d1",
        "relay_credential_id": "",
        "relay_private_key_path": "",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _FakeHandshakeError(Exception):
    """Mimics aiohttp.WSServerHandshakeError (carries a ``.status``)."""

    def __init__(self, status):
        super().__init__(f"handshake rejected: {status}")
        self.status = status


def _fake_deps(**overrides):
    """Deps namespace matching ``_import_relay_probe_deps`` output.

    Defaults to the REAL ``_is_tls_verify_error`` so the ca_missing mapping is
    genuinely classified; network + crypto pieces are stubbed.
    """

    base = dict(
        build_relay_ssl_context=lambda: (ssl.create_default_context(), True),
        is_tls_verify_error=_is_tls_verify_error,
        build_daemon_ws_url=lambda url, did: "wss://relay.example/v1/daemon/d1/ws",
        load_private_key_pem_from_config=lambda cfg: "PEM",
        make_daemon_attach_headers=lambda **kw: {"Authorization": "sig"},
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


class RelayProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(
            importlib.import_module("torque.cloud_hooks")
        )
        # Empty data_dir (no ee_connector.json) + cleared connector env so that
        # only the SimpleNamespace settings drive the resolved config.
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = self._tmp.name
        env_patch = mock.patch.dict(
            os.environ,
            {"TORQUE_EE_DAEMON_CREDENTIAL_ID": ""},
            clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        cfg = self.cloud_hooks.torque_config
        for attr in ("CLOUD_CONNECTOR_ENABLED", "CLOUD_RELAY_URL", "CLOUD_DAEMON_ID"):
            p = mock.patch.object(cfg, attr, "" if attr != "CLOUD_CONNECTOR_ENABLED" else False)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    async def _probe(self, settings):
        return await self.cloud_hooks.probe_relay_connection(
            settings, data_dir=self.data_dir
        )

    async def test_disabled_when_relay_off(self):
        result = await self._probe(_settings(relay_enabled=False))
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_DISABLED)
        self.assertIn("disabled", result["message"].lower())

    async def test_misconfigured_when_no_relay_url(self):
        result = await self._probe(_settings(relay_url=""))
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_MISCONFIGURED)
        self.assertIn("relay url", result["message"].lower())

    async def test_misconfigured_when_connector_deps_absent(self):
        with mock.patch.object(
            self.cloud_hooks,
            "_import_relay_probe_deps",
            side_effect=ImportError("No module named 'torque_ee_connector'"),
        ):
            result = await self._probe(_settings())
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_MISCONFIGURED)
        self.assertIn("connector", result["message"].lower())

    async def test_unreachable_on_connection_error(self):
        async def boom(url, *, ssl_ctx, timeout):
            raise ConnectionError("name resolution failed")

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=boom):
            result = await self._probe(_settings())
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_UNREACHABLE)

    async def test_ca_missing_on_tls_verify_error(self):
        async def tls_fail(url, *, ssl_ctx, timeout):
            raise ssl.SSLCertVerificationError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "unable to get local issuer certificate"
            )

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=tls_fail):
            result = await self._probe(_settings())
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_CA_MISSING)
        # Actionable: names certifi / SSL_CERT_FILE rather than a generic fail.
        self.assertTrue(
            "certifi" in result["message"].lower()
            or "ssl_cert_file" in result["message"].lower()
        )

    async def test_unreachable_on_non_200_health(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 502, "json": None}

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health):
            result = await self._probe(_settings())
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_UNREACHABLE)
        self.assertIn("502", result["detail"])

    async def test_reachable_unauthed_when_no_credentials(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 200, "json": {"auth_mode": "required"}}

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health):
            # No credential_id / private_key_path configured.
            result = await self._probe(_settings())
        self.assertEqual(
            result["status"], self.cloud_hooks.RELAY_PROBE_REACHABLE_UNAUTHED
        )

    async def test_ok_on_successful_attach(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 200, "json": {"auth_mode": "required"}}

        async def attach(ws_url, *, headers, ssl_ctx, timeout):
            return {"status_code": 101}

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_attach", side_effect=attach):
            result = await self._probe(
                _settings(relay_credential_id="cred-1", relay_private_key_path="/keys/relay.pem")
            )
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_OK)

    async def test_auth_rejected_on_4xx_handshake(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 200, "json": {"auth_mode": "required"}}

        async def attach(ws_url, *, headers, ssl_ctx, timeout):
            raise _FakeHandshakeError(401)

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_attach", side_effect=attach):
            result = await self._probe(
                _settings(relay_credential_id="cred-1", relay_private_key_path="/keys/relay.pem")
            )
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_AUTH_REJECTED)
        self.assertIn("401", result["message"])

    async def test_ca_missing_on_attach_tls_error(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 200, "json": {"auth_mode": "required"}}

        async def attach(ws_url, *, headers, ssl_ctx, timeout):
            raise ssl.SSLCertVerificationError("certificate verify failed")

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_attach", side_effect=attach):
            result = await self._probe(
                _settings(relay_credential_id="cred-1", relay_private_key_path="/keys/relay.pem")
            )
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_CA_MISSING)

    async def test_misconfigured_when_private_key_unreadable(self):
        async def health(url, *, ssl_ctx, timeout):
            return {"status_code": 200, "json": {"auth_mode": "required"}}

        def bad_key(cfg):
            raise ValueError(
                "daemon private key file must not be group/world accessible; "
                "expected mode 0600"
            )

        deps = _fake_deps(load_private_key_pem_from_config=bad_key)
        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=deps), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=health):
            result = await self._probe(
                _settings(relay_credential_id="cred-1", relay_private_key_path="/keys/relay.pem")
            )
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_MISCONFIGURED)
        self.assertIn("private key", result["message"].lower())

    async def test_result_shape_always_has_three_keys(self):
        # The command merges this into {"type": "relay_test_result", ...}; the
        # contract is exactly {status, message, detail}.
        result = await self._probe(_settings(relay_enabled=False))
        self.assertEqual(set(result.keys()), {"status", "message", "detail"})
        for value in result.values():
            self.assertIsInstance(value, str)

    async def test_timeout_maps_to_unreachable(self):
        import asyncio

        async def hang(url, *, ssl_ctx, timeout):
            await asyncio.sleep(5)
            return {"status_code": 200, "json": {}}

        with mock.patch.object(self.cloud_hooks, "_import_relay_probe_deps", return_value=_fake_deps()), \
             mock.patch.object(self.cloud_hooks, "_relay_probe_health", side_effect=hang):
            result = await self.cloud_hooks.probe_relay_connection(
                _settings(), data_dir=self.data_dir, timeout=0.05
            )
        self.assertEqual(result["status"], self.cloud_hooks.RELAY_PROBE_UNREACHABLE)


if __name__ == "__main__":
    unittest.main()
