"""Signed attach helpers for the enterprise relay connector.

This module intentionally imports ``cryptography`` lazily so community/runtime
environments without EE dependencies can import ``torque_ee_connector`` without
crashing. Signed remote attach is disabled cleanly when the dependency is absent.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

DAEMON_ATTACH_AUTH_SCHEME = "Torque-Daemon-Signature"
DAEMON_ATTACH_AUTH_VERSION = "v1"
EMPTY_SHA256_HEX = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class CryptoUnavailableError(RuntimeError):
    """Raised when signed attach is requested without EE crypto deps."""


def cryptography_available() -> bool:
    try:
        _crypto_modules()
    except CryptoUnavailableError:
        return False
    return True


def canonical_daemon_attach_string(
    *,
    method: str,
    path: str,
    daemon_id: str,
    credential_id: str,
    timestamp: str,
    nonce: str,
    body_sha256_hex: str = EMPTY_SHA256_HEX,
) -> str:
    return "\n".join((
        "torque-daemon-attach-v1",
        _required(method, "method").upper(),
        _required(path, "path"),
        f"daemon_id:{_required(daemon_id, 'daemon_id')}",
        f"credential_id:{_required(credential_id, 'credential_id')}",
        f"timestamp:{_required(timestamp, 'timestamp')}",
        f"nonce:{_required(nonce, 'nonce')}",
        f"body_sha256:{body_sha256_hex or EMPTY_SHA256_HEX}",
    ))


def make_daemon_attach_headers(
    *,
    ws_url: str,
    daemon_id: str,
    credential_id: str,
    private_key_pem: str,
    timestamp: str = "",
    nonce: str = "",
) -> dict[str, str]:
    """Return Authorization headers for a signed daemon WebSocket attach."""

    timestamp = timestamp or _now_iso()
    nonce = nonce or f"nonce-{os.urandom(16).hex()}"
    parsed = urlparse(_required(ws_url, "ws_url"))
    canonical = canonical_daemon_attach_string(
        method="GET",
        path=parsed.path,
        daemon_id=daemon_id,
        credential_id=credential_id,
        timestamp=timestamp,
        nonce=nonce,
    )
    signature = _sign_es256_raw(private_key_pem, canonical.encode("utf-8"))
    return {
        "Authorization": (
            f'{DAEMON_ATTACH_AUTH_SCHEME} {DAEMON_ATTACH_AUTH_VERSION} '
            f'credential_id="{_escape_header(credential_id)}", '
            f'timestamp="{_escape_header(timestamp)}", '
            f'nonce="{_escape_header(nonce)}", '
            f'signature="{_escape_header(signature)}"'
        )
    }


def load_private_key_pem_from_config(config: dict[str, Any]) -> str:
    inline = str(config.get("private_key_pem") or config.get("daemon_private_key_pem") or "").strip()
    if inline:
        return inline
    path = str(config.get("private_key_path") or config.get("daemon_private_key_path") or "").strip()
    if not path:
        return ""
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError as exc:
        raise ValueError(f"daemon private key file cannot be read: {path}") from exc
    if mode & 0o077:
        raise ValueError("daemon private key file must not be group/world accessible; expected mode 0600")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _sign_es256_raw(private_key_pem: str, payload: bytes) -> str:
    serialization, ec, hashes, decode_dss_signature = _crypto_modules()
    try:
        key = serialization.load_pem_private_key(
            _required(private_key_pem, "private_key_pem").encode("utf-8"),
            password=None,
        )
    except Exception as exc:  # pragma: no cover - exact crypto error varies
        raise ValueError("daemon private key could not be loaded") from exc
    signature_der = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r_value, s_value = decode_dss_signature(signature_der)
    raw = r_value.to_bytes(32, "big") + s_value.to_bytes(32, "big")
    return _base64url(raw)


def _crypto_modules():
    try:
        from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
        from cryptography.hazmat.primitives.asymmetric import ec, utils  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised with import hook test
        raise CryptoUnavailableError(
            "cryptography is required for signed remote relay attach; install EE connector deps or use loopback local-dev mode"
        ) from exc
    return serialization, ec, hashes, utils.decode_dss_signature


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _escape_header(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')
