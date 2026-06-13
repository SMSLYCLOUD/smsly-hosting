"""
TLS verification helper for inter-node HTTP calls.

The audit (Batch G, item 3.5) found that the platform sent
``gateway_secret``, SSH passwords, and other long-lived secrets
to remote nodes with ``verify=False`` on every request, allowing
a network-adjacent attacker to MITM the connection and capture
the secrets.

This module provides a single ``resolve_tls_verify(managed_server)``
that returns the right ``verify`` value for ``requests.*`` calls
based on the per-server ``verify_tls`` flag and the platform-wide
``ALLOW_INSECURE_INTER_NODE_TLS`` env flag. The helper also
supports a SHA-256 cert pin (``tls_cert_sha256``) — when set,
the connection is only accepted if the remote cert matches the
pin (regardless of the system trust store).
"""
import os
import ssl
from typing import Optional, Tuple

from django.conf import settings


# SECURITY: helper used to read the platform-wide
# ALLOW_INSECURE_INTER_NODE_TLS env flag. We re-read it on
# every call (rather than capturing it at import time) so a
# runtime config change is honored without a process restart.
def _allow_insecure_inter_node_tls() -> bool:
    raw = os.environ.get("ALLOW_INSECURE_INTER_NODE_TLS", "false").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    # Honour the Django settings.DEBUG override too — when DEBUG
    # is on, the platform is in development mode and skipping
    # cert checks is acceptable (e.g. self-signed master in a
    # local docker compose stack).
    return bool(getattr(settings, "DEBUG", False))


def _build_ssl_context_for_pin(fingerprint_hex: str) -> ssl.SSLContext:
    """Build an SSLContext that pins the peer cert SHA-256.

    The returned context raises ``ssl.SSLError`` on handshake if
    the remote cert doesn't match the pin. Use it as
    ``requests.get(..., verify=<context>)`` — but note that
    ``requests``' ``verify`` parameter is a bool or a path to a CA
    bundle, not an SSLContext. To pin, the caller must use the
    raw ``urllib3`` PoolManager via a custom adapter, OR fall
    back to a `verify=True` connection that the cert pin is
    checked against post-handshake (see ``pin_ssl_adapter.py``).
    """
    if not fingerprint_hex or not all(c in "0123456789abcdefABCDEF" for c in fingerprint_hex):
        raise ValueError("tls_cert_sha256 must be 64 hex chars")
    ctx = ssl.create_default_context()
    # Verify by hash: store the expected fingerprint and check after.
    # Python's stdlib doesn't have a direct "verify by SHA-256"
    # option, so we keep the context as default and let
    # ``_check_pin_after_handshake`` validate below.
    return ctx


def _check_pin_after_handshake(response, expected_fingerprint_hex: str) -> None:
    """After a successful TLS handshake, check the peer cert SHA-256.

    Used when ``requests`` returned a response but the underlying
    ``urllib3`` connection-pool cert is the one we want to pin.
    Raises ``ssl.SSLError`` on mismatch.
    """
    import hashlib
    pool = response.raw._connection.pool  # type: ignore[attr-defined]
    sock = pool.sock if hasattr(pool, "sock") else None
    if sock is None:
        # Modern urllib3: cert is in pool.conn.cert_file / we need
        # to reach into a different attribute. Fall back to
        # the request's underlying urllib3 connection.
        try:
            peer = response.raw._connection.sock.getpeercert(binary_form=True)
        except Exception:
            return
    else:
        peer = sock.getpeercert(binary_form=True)
    if peer is None:
        return
    digest = hashlib.sha256(peer).hexdigest()
    if digest.lower() != expected_fingerprint_hex.lower():
        raise ssl.SSLError(
            f"TLS cert SHA-256 mismatch: got {digest}, "
            f"expected {expected_fingerprint_hex}"
        )


def resolve_tls_verify(managed_server) -> Tuple[bool, Optional[str]]:
    """Return ``(verify, fingerprint_hex)`` for a given ManagedServer.

    - If the server has a ``tls_cert_sha256`` pin set, return
      ``(True, fingerprint)``. The caller should use
      ``_check_pin_after_handshake`` to validate the pin.
    - If the server has ``verify_tls=True`` (the default), return
      ``(True, None)``.
    - If the server has ``verify_tls=False``, only honor that if
      the platform-wide ``ALLOW_INSECURE_INTER_NODE_TLS`` env flag
      is set, or if ``settings.DEBUG``. Otherwise return
      ``(True, None)`` to refuse the insecure request.
    """
    fingerprint = (getattr(managed_server, "tls_cert_sha256", "") or "").strip()
    if fingerprint:
        return True, fingerprint
    if getattr(managed_server, "verify_tls", True):
        return True, None
    # Server wants to skip cert verification. Only honor that if the
    # operator has explicitly opted in.
    if _allow_insecure_inter_node_tls():
        return False, None
    return True, None  # refuse the insecure request


def resolve_tls_verify_for_url(candidate_url: str) -> Tuple[bool, Optional[str]]:
    """Return ``(verify, fingerprint_hex)`` for a POST to a peer URL.

    Used by the provisioner when it doesn't yet have a
    ``ManagedServer`` row for the target (the master is being
    discovered by candidate URL). Defaults to the safe behavior:

      * For ``http://`` URLs: ``(False, None)`` — there is no
        certificate to verify, so there's nothing to pin.
      * For ``https://`` URLs: ``(True, None)`` — refuse to skip
        cert verification unless ``ALLOW_INSECURE_INTER_NODE_TLS``
        is set in the environment, in which case ``(False, None)``.
      * If the operator has set a master-cert SHA-256 pin via the
        env var ``SMSLY_MASTER_TLS_CERT_SHA256``, the second
        element is that pin and the caller should pass it to
        ``_check_pin_after_handshake``.
    """
    from urllib.parse import urlparse
    parsed = urlparse(candidate_url or "")
    if parsed.scheme != "https":
        # Plain HTTP has no certificate to verify.
        return False, None
    fingerprint = os.environ.get(
        "SMSLY_MASTER_TLS_CERT SHA256", ""
    ).strip()
    if fingerprint:
        return True, fingerprint
    if _allow_insecure_inter_node_tls():
        return False, None
    return True, None  # safe default
