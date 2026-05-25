import base64
import importlib
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
except ModuleNotFoundError:  # Community test environments may not bootstrap deps.
    serialization = None
    ec = None

_CRYPTOGRAPHY_AVAILABLE = serialization is not None and ec is not None

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def _settings(**overrides):
    base = {
        "relay_enabled": True,
        "relay_url": "https://relay.example",
        "relay_daemon_id": "daemon-1",
        "relay_credential_id": "",
        "relay_private_key_path": "",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


class _FakePairResponse:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self._body = body
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def text(self):
        return self._text


class _FakePairSession:
    queue = []
    calls = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None):
        self.__class__.calls.append({"url": url, "json": json})
        if not self.__class__.queue:
            raise AssertionError("no fake /v1/pair response queued")
        response = self.__class__.queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClientTimeout:
    def __init__(self, total=None):
        self.total = total


class DaemonCredentialKeygenTests(unittest.TestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(importlib.import_module("torque.cloud_hooks"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @unittest.skipUnless(
        _CRYPTOGRAPHY_AVAILABLE,
        "cryptography not installed in this Python environment",
    )
    def test_keygen_writes_loadable_p256_pkcs8_pem_and_public_only_jwk(self):
        result = self.cloud_hooks.generate_daemon_keypair(
            "daemon-1", data_dir=self.tmp.name
        )
        key_path = Path(result["private_key_path"])
        self.assertEqual(
            key_path,
            Path(self.tmp.name) / "relay" / "daemon-daemon-1.pem",
        )
        self.assertTrue(key_path.exists())
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)

        pem = key_path.read_bytes()
        self.assertIn(b"-----BEGIN PRIVATE KEY-----", pem)
        private_key = serialization.load_pem_private_key(pem, password=None)
        self.assertIsInstance(private_key.curve, ec.SECP256R1)

        jwk = result["public_key_jwk"]
        self.assertEqual(jwk["kty"], "EC")
        self.assertEqual(jwk["crv"], "P-256")
        self.assertEqual(jwk["alg"], "ES256")
        self.assertEqual(jwk["key_ops"], ["verify"])
        self.assertNotIn("d", jwk)
        for coord in ("x", "y"):
            padded = jwk[coord] + ("=" * ((4 - len(jwk[coord]) % 4) % 4))
            self.assertEqual(len(base64.urlsafe_b64decode(padded)), 32)

    def test_commit_staged_key_uses_credential_path_without_clobbering_default(self):
        relay_dir = Path(self.tmp.name) / "relay"
        relay_dir.mkdir(parents=True)
        active_path = relay_dir / "daemon-daemon-1.pem"
        active_path.write_bytes(b"existing-active-key")
        os.chmod(active_path, 0o600)
        staged_path = relay_dir / ".daemon-daemon-1-stage.pem.tmp"
        staged_path.write_bytes(b"new-accepted-key")
        os.chmod(staged_path, 0o600)

        final_path = Path(
            self.cloud_hooks._commit_staged_daemon_keypair(
                str(staged_path),
                "daemon-1",
                "cred-1",
                data_dir=self.tmp.name,
            )
        )

        self.assertEqual(
            final_path,
            relay_dir / "daemon-daemon-1-cred-1.pem",
        )
        self.assertEqual(final_path.read_bytes(), b"new-accepted-key")
        self.assertEqual(stat.S_IMODE(final_path.stat().st_mode), 0o600)
        self.assertFalse(staged_path.exists())
        self.assertEqual(active_path.read_bytes(), b"existing-active-key")


class RelayPairUrlTests(unittest.TestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(importlib.import_module("torque.cloud_hooks"))

    def test_pair_url_derivation_variants(self):
        cases = [
            ("relay.example", "https://relay.example/v1/pair"),
            ("http://relay.example", "http://relay.example/v1/pair"),
            ("https://relay.example/base", "https://relay.example/base/v1/pair"),
            (
                "ws://relay.example/v1/daemon/daemon-1/ws",
                "http://relay.example/v1/pair",
            ),
            (
                "wss://relay.example/scope/v1/daemon/daemon-1/ws",
                "https://relay.example/scope/v1/pair",
            ),
            ("https://relay.example/v1/pair", "https://relay.example/v1/pair"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.cloud_hooks._relay_pair_url(raw), expected)


class GenerateDaemonCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cloud_hooks = importlib.reload(importlib.import_module("torque.cloud_hooks"))
        self._old_aiohttp = sys.modules.get("aiohttp")
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.ClientSession = _FakePairSession
        aiohttp.ClientTimeout = _FakeClientTimeout
        sys.modules["aiohttp"] = aiohttp
        _FakePairSession.queue = []
        _FakePairSession.calls = []
        self._patches = [
            mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_RELAY_URL", ""),
            mock.patch.object(self.cloud_hooks.torque_config, "CLOUD_DAEMON_ID", ""),
            mock.patch.dict(os.environ, {"TORQUE_EE_DAEMON_CREDENTIAL_ID": ""}, clear=False),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(getattr(self, "_patches", [])):
            patcher.stop()
        if self._old_aiohttp is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = self._old_aiohttp

    async def test_success_posts_public_jwk_to_v1_pair_and_returns_credential(self):
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": "xcoord",
            "y": "ycoord",
            "key_ops": ["verify"],
            "alg": "ES256",
        }
        _FakePairSession.queue.append(
            _FakePairResponse(
                201,
                {
                    "credential_id": "cred-1",
                    "daemon_id": "daemon-1",
                    "owner_user_id": "owner-1",
                },
            )
        )
        with mock.patch.object(
            self.cloud_hooks,
            "_stage_daemon_keypair",
            return_value={"private_key_path": "/tmp/staged-daemon-1.pem", "public_key_jwk": jwk},
        ) as stage, mock.patch.object(
            self.cloud_hooks,
            "_commit_staged_daemon_keypair",
            return_value="/tmp/daemon-1-cred-1.pem",
        ) as commit:
            result = await self.cloud_hooks.generate_daemon_credential(
                _settings(),
                pairing_token=" pair-token ",
                data_dir="/tmp/profile",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["credential_id"], "cred-1")
        self.assertEqual(result["private_key_path"], "/tmp/daemon-1-cred-1.pem")
        self.assertEqual(result["owner_user_id"], "owner-1")
        self.assertEqual(result["provenance"]["private_key_path"], "local_keygen")
        stage.assert_called_once_with("daemon-1", data_dir="/tmp/profile")
        commit.assert_called_once_with(
            "/tmp/staged-daemon-1.pem",
            "daemon-1",
            "cred-1",
            data_dir="/tmp/profile",
        )

        self.assertEqual(len(_FakePairSession.calls), 1)
        call = _FakePairSession.calls[0]
        self.assertEqual(call["url"], "https://relay.example/v1/pair")
        self.assertEqual(call["json"]["pairing_token"], "pair-token")
        self.assertEqual(call["json"]["daemon_id"], "daemon-1")
        self.assertEqual(call["json"]["public_key_jwk"], jwk)
        self.assertNotIn("credential_id", call["json"])
        self.assertNotIn("owner_user_id", call["json"])
        self.assertNotIn("d", call["json"]["public_key_jwk"])

    async def test_401_and_403_map_to_actionable_pairing_errors(self):
        cases = [
            (401, "pairing_token_rejected", "invalid, expired, or already used"),
            (403, "pairing_token_daemon_mismatch", "different daemon ID"),
        ]
        for status_code, error, message in cases:
            with self.subTest(status_code=status_code):
                _FakePairSession.queue = [_FakePairResponse(status_code, {"error": error})]
                _FakePairSession.calls = []
                with mock.patch.object(
                    self.cloud_hooks,
                    "_stage_daemon_keypair",
                    return_value={
                        "private_key_path": "/tmp/staged-daemon-1.pem",
                        "public_key_jwk": {"kty": "EC", "crv": "P-256", "x": "x", "y": "y"},
                    },
                ), mock.patch.object(
                    self.cloud_hooks,
                    "_commit_staged_daemon_keypair",
                ) as commit:
                    result = await self.cloud_hooks.generate_daemon_credential(
                        _settings(), pairing_token="pair-token"
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], error)
                self.assertIn(message, result["message"])
                self.assertEqual(len(_FakePairSession.calls), 1)
                commit.assert_not_called()

    async def test_unset_relay_url_or_daemon_id_errors_before_keygen(self):
        with mock.patch.object(self.cloud_hooks, "_stage_daemon_keypair") as keygen:
            result = await self.cloud_hooks.generate_daemon_credential(
                _settings(relay_url="", relay_daemon_id=""),
                pairing_token="pair-token",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "misconfigured")
        self.assertIn("missing=relay_url,daemon_id", result["detail"])
        keygen.assert_not_called()
        self.assertEqual(_FakePairSession.calls, [])

    async def test_missing_crypto_dependency_returns_clear_error_before_post(self):
        with mock.patch.object(
            self.cloud_hooks,
            "_stage_daemon_keypair",
            side_effect=ImportError("No module named cryptography"),
        ):
            result = await self.cloud_hooks.generate_daemon_credential(
                _settings(), pairing_token="pair-token"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "crypto_missing")
        self.assertIn("cryptography", result["message"].lower())
        self.assertEqual(_FakePairSession.calls, [])

    async def test_key_write_failure_is_distinct_from_missing_crypto(self):
        with mock.patch.object(
            self.cloud_hooks,
            "_stage_daemon_keypair",
            side_effect=PermissionError("permission denied"),
        ):
            result = await self.cloud_hooks.generate_daemon_credential(
                _settings(), pairing_token="pair-token"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "key_write_failed")
        self.assertIn("private-key staging file", result["message"])
        self.assertEqual(_FakePairSession.calls, [])

    async def test_failed_pairing_preserves_existing_active_key_and_cleans_staging(self):
        cases = [
            ("401", _FakePairResponse(401, {"error": "bad"}), "pairing_token_rejected"),
            ("403", _FakePairResponse(403, {"error": "mismatch"}), "pairing_token_daemon_mismatch"),
            ("unreachable", ConnectionError("offline"), "relay_unreachable"),
            (
                "invalid-response",
                _FakePairResponse(201, {"daemon_id": "daemon-1"}),
                "invalid_response",
            ),
        ]
        for label, fake_response, expected_error in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                relay_dir = Path(tmp) / "relay"
                relay_dir.mkdir(parents=True)
                active_path = relay_dir / "daemon-daemon-1.pem"
                active_path.write_bytes(b"existing-valid-key")
                os.chmod(active_path, 0o600)
                staged_path = relay_dir / ".daemon-daemon-1-new.pem.tmp"

                def fake_stage(daemon_id, *, data_dir=""):
                    self.assertEqual(daemon_id, "daemon-1")
                    self.assertEqual(data_dir, tmp)
                    staged_path.write_bytes(b"new-unregistered-key")
                    os.chmod(staged_path, 0o600)
                    return {
                        "private_key_path": str(staged_path),
                        "public_key_jwk": {
                            "kty": "EC",
                            "crv": "P-256",
                            "x": "x",
                            "y": "y",
                            "key_ops": ["verify"],
                            "alg": "ES256",
                        },
                    }

                _FakePairSession.queue = [fake_response]
                _FakePairSession.calls = []
                with mock.patch.object(
                    self.cloud_hooks, "_stage_daemon_keypair", side_effect=fake_stage
                ), mock.patch.object(
                    self.cloud_hooks, "_commit_staged_daemon_keypair"
                ) as commit:
                    result = await self.cloud_hooks.generate_daemon_credential(
                        _settings(
                            relay_private_key_path=str(active_path),
                            relay_credential_id="existing-cred",
                        ),
                        pairing_token="pair-token",
                        data_dir=tmp,
                    )

                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], expected_error)
                self.assertEqual(active_path.read_bytes(), b"existing-valid-key")
                self.assertFalse(staged_path.exists())
                commit.assert_not_called()


class GenerateDaemonCredentialCommandTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_success_writes_only_credential_id_and_private_key_path_to_settings(self):
        updates = []
        restarts = []

        class FakeState:
            def __init__(self):
                self.global_settings = _settings()

            async def update_global_settings_durable(self, **fields):
                updates.append(dict(fields))
                for key, value in fields.items():
                    setattr(self.global_settings, key, value)

        fake_state = FakeState()

        def fingerprint():
            gs = fake_state.global_settings
            return (
                bool(gs.relay_enabled),
                gs.relay_url,
                gs.relay_daemon_id,
                gs.relay_credential_id,
                gs.relay_private_key_path,
            )

        async def restart():
            restarts.append(True)

        handle_command = self._extract_handle_command(
            state=fake_state,
            _relay_settings_fingerprint=fingerprint,
            _restart_cloud_connector=restart,
        )
        generated = {
            "ok": True,
            "credential_id": "cred-1",
            "daemon_id": "daemon-1",
            "owner_user_id": "owner-1",
            "private_key_path": "/tmp/profile/relay/daemon-daemon-1.pem",
            "provenance": {"private_key_path": "local_keygen"},
        }
        with mock.patch.object(
            self.server_mod.cloud_hooks,
            "generate_daemon_credential",
            mock.AsyncMock(return_value=generated),
        ) as gen:
            result = await handle_command({
                "cmd": "generate_daemon_credential",
                "pairing_token": "pair-token",
            })

        gen.assert_awaited_once()
        self.assertEqual(result["type"], "daemon_credential")
        self.assertTrue(result["ok"])
        self.assertEqual(result["credential_id"], "cred-1")
        self.assertEqual(updates, [{
            "relay_credential_id": "cred-1",
            "relay_private_key_path": "/tmp/profile/relay/daemon-daemon-1.pem",
        }])
        self.assertEqual(restarts, [True])
        self.assertEqual(fake_state.global_settings.relay_credential_id, "cred-1")
        self.assertEqual(
            fake_state.global_settings.relay_private_key_path,
            "/tmp/profile/relay/daemon-daemon-1.pem",
        )
        self.assertIn("relay_config", result)

    async def test_durable_settings_write_failure_returns_recoverable_credential_handle(self):
        durable_write_attempts = []

        class BadSettingsDB:
            async def save_global_settings_durable(self, gs):
                durable_write_attempts.append(
                    {
                        "relay_credential_id": gs.relay_credential_id,
                        "relay_private_key_path": gs.relay_private_key_path,
                    }
                )
                raise OSError("settings database is read-only")

        fake_state = self.state_mod.MatrixState(db=BadSettingsDB())
        fake_state.global_settings = self.state_mod.GlobalSettings(
            relay_enabled=True,
            relay_url="https://relay.example",
            relay_daemon_id="daemon-1",
        )

        def fingerprint():
            gs = fake_state.global_settings
            return (
                bool(gs.relay_enabled),
                gs.relay_url,
                gs.relay_daemon_id,
                gs.relay_credential_id,
                gs.relay_private_key_path,
            )

        restart = mock.AsyncMock()
        handle_command = self._extract_handle_command(
            state=fake_state,
            _relay_settings_fingerprint=fingerprint,
            _restart_cloud_connector=restart,
        )
        generated = {
            "ok": True,
            "credential_id": "cred-orphan",
            "daemon_id": "daemon-1",
            "owner_user_id": "owner-1",
            "private_key_path": "/tmp/profile/relay/daemon-daemon-1-cred-orphan.pem",
            "provenance": {"private_key_path": "local_keygen"},
        }
        with mock.patch.object(
            self.server_mod.cloud_hooks,
            "generate_daemon_credential",
            mock.AsyncMock(return_value=generated),
        ), self.assertLogs("torque", level="ERROR") as logs:
            result = await handle_command({
                "cmd": "generate_daemon_credential",
                "pairing_token": "pair-token",
            })

        self.assertEqual(result["type"], "daemon_credential")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "settings_write_failed")
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["credential_id"], "cred-orphan")
        self.assertEqual(
            result["private_key_path"],
            "/tmp/profile/relay/daemon-daemon-1-cred-orphan.pem",
        )
        self.assertIn("Relay accepted the credential", result["message"])
        self.assertIn("relay admin to revoke", result["message"])
        self.assertEqual(durable_write_attempts, [{
            "relay_credential_id": "cred-orphan",
            "relay_private_key_path": "/tmp/profile/relay/daemon-daemon-1-cred-orphan.pem",
        }])
        restart.assert_not_awaited()
        self.assertEqual(fake_state.global_settings.relay_credential_id, "")
        self.assertEqual(fake_state.global_settings.relay_private_key_path, "")
        self.assertIn("relay_config", result)
        joined_logs = "\n".join(logs.output)
        self.assertIn("cred-orphan", joined_logs)
        self.assertIn(
            "/tmp/profile/relay/daemon-daemon-1-cred-orphan.pem",
            joined_logs,
        )


if __name__ == "__main__":
    unittest.main()
