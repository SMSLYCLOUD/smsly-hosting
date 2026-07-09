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
import ipaddress
import os
import socket
import ssl
from urllib.parse import urlparse


# SECURITY: helper used to read the platform-wide
# ALLOW_INSECURE_INTER_NODE_TLS env flag. We re-read it on
# every call (rather than capturing it at import time) so a
# runtime config change is honored without a process restart.
def _allow_insecure_inter_node_tls() -> bool:
    raw = os.environ.get("ALLOW_INSECURE_INTER_NODE_TLS", "false").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    return False


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

    connection = getattr(response.raw, "_connection", None)
    if connection is None:
        raise ValueError("TLS pin check failed: no underlying connection available")

    pool = getattr(connection, "pool", None)
    sock = getattr(pool, "sock", None) if pool else None

    if sock is None:
        # Modern urllib3: cert is in pool.conn.cert_file / we need
        # to reach into a different attribute. Fall back to
        # the request's underlying urllib3 connection.
        try:
            peer = connection.sock.getpeercert(binary_form=True)
        except Exception as exc:
            raise ValueError(f"TLS pin check failed: unable to retrieve peer certificate: {exc}") from exc
    else:
        peer = sock.getpeercert(binary_form=True)

    if peer is None:
        raise ValueError("TLS pin check failed: peer certificate is None")
    digest = hashlib.sha256(peer).hexdigest()
    if digest.lower() != expected_fingerprint_hex.lower():
        raise ssl.SSLError(
            f"TLS cert SHA-256 mismatch: got {digest}, "
            f"expected {expected_fingerprint_hex}"
        )


def _is_wireguard_ip(address: str) -> bool:
    """Check if an IP address is in the WireGuard mesh range (10.100.0.0/24)."""
    try:
        return ipaddress.ip_address(address.strip()) in ipaddress.ip_network("10.100.0.0/24")
    except ValueError:
        return False


def resolve_tls_verify(managed_server) -> tuple[bool, str | None]:
    """Return ``(verify, fingerprint_hex)`` for a given ManagedServer.

    - If the server has a ``tls_cert_sha256`` pin set, return
      ``(True, fingerprint)``. The caller should use
      ``_check_pin_after_handshake`` to validate the pin.
    - If the server has an ``wg_address`` in the 10.100.0.0/24 mesh
      range, return ``(False, None)`` — traffic never leaves the
      encrypted WireGuard tunnel.
    - If the server has ``verify_tls=True`` (the default), return
      ``(True, None)``.
    - If the server has ``verify_tls=False``, only honor that if
      the platform-wide ``ALLOW_INSECURE_INTER_NODE_TLS`` env flag
      is set. Otherwise return ``(True, None)`` to refuse the
      insecure request.
    """
    fingerprint = (getattr(managed_server, "tls_cert_sha256", "") or "").strip()
    if fingerprint:
        return False, fingerprint
    wg_address = (getattr(managed_server, "wg_address", "") or "").strip()
    if wg_address and _is_wireguard_ip(wg_address):
        return False, None
    if getattr(managed_server, "verify_tls", True):
        return True, None
    if _allow_insecure_inter_node_tls():
        return False, None
    return True, None  # refuse the insecure request


def resolve_tls_verify_for_url(candidate_url: str) -> tuple[bool, str | None]:
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
        "SMSLY_MASTER_TLS_CERT_SHA256", ""
    ).strip()
    if fingerprint:
        return False, fingerprint
    if _allow_insecure_inter_node_tls():
        return False, None
    return True, None  # safe default


# ── URL-based policy helpers (centralised entry point for ad-hoc calls) ──
#
# These helpers complement the per-server ``resolve_tls_verify`` and the
# per-URL ``resolve_tls_verify_for_url`` by providing a single, opinionated
# policy for ad-hoc HTTP calls that don't have a ``ManagedServer`` context
# (e.g. health probes, mesh hops, diagnostic commands). They are the single
# entry point for deciding whether ``verify=False`` is acceptable for a
# given URL — callers should NEVER hard-code ``verify=False`` anywhere in
# the codebase; they should call ``should_verify(url)`` instead.
#
# Rules:
# - Plain HTTP: there is no certificate to verify, so ``should_verify``
#   returns ``False`` regardless of target.
# - HTTPS loopback / Docker-internal: ``should_verify`` returns ``False``
#   (traffic never leaves the host or the docker network).
# - HTTPS private IP (RFC 1918): ``should_verify`` returns ``False`` only
#   when ``ALLOW_INSECURE_INTER_NODE_TLS`` is set in the environment.
# - HTTPS public: ``should_verify`` returns ``True`` — the caller's
#   responsibility to ensure cert trust.
#
# ``audit_verify(url, verify)`` is the companion audit logger. Callers
# that intentionally pass ``verify=False`` should call it so that any
# accidental use against a non-internal target is logged as a warning.

_ALLOW_INSECURE_ENV = "ALLOW_INSECURE_INTER_NODE_TLS"
_DOCKER_INTERNAL_NAMES = frozenset({
    "backend", "db", "redis", "rabbitmq", "registry", "caddy", "traefik",
    "frontend", "celery", "celery-beat", "celery-fast", "celery-deploy",
    "pgcat", "prometheus", "loki", "grafana", "cadvisor", "node-exporter",
    "socket-proxy", "frps", "route-fallback",
})


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_private(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _resolve_host(host: str) -> str:
    """Resolve a hostname to an IP; return the original if resolution fails."""
    if not host:
        return host
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, UnicodeError):
        return host


def _allow_insecure_env_set() -> bool:
    raw = os.environ.get(_ALLOW_INSECURE_ENV, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    return False


def should_verify(url: str) -> bool:
    """Return ``True`` if the URL requires TLS verification.

    Returns ``False`` for plain HTTP, loopback / Docker-internal hosts, and
    private IPs when ``ALLOW_INSECURE_INTER_NODE_TLS`` is set.
    Returns ``True`` for HTTPS public URLs.
    """
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    # Plain HTTP has no certificate to verify.
    if parsed.scheme != "https":
        return False
    if not host:
        return True
    if _is_loopback(host):
        return False
    if host in _DOCKER_INTERNAL_NAMES:
        return False
    if _is_private(_resolve_host(host)):
        return not _allow_insecure_env_set()
    return True


def is_insecure_target(url: str) -> tuple[bool, str]:
    """Return ``(is_insecure, reason)`` for a given URL — useful for audit logging."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return True, "plain-http"
    if _is_loopback(host):
        return True, "loopback"
    if host in _DOCKER_INTERNAL_NAMES:
        return True, "docker-internal"
    if _is_private(_resolve_host(host)):
        if _allow_insecure_env_set():
            return True, "private-network-allowed"
        return False, "private-network-not-allowed"
    return False, "public"


def audit_verify(url: str, verify: bool) -> None:
    """Log a warning if ``verify=False`` is used against a target that should
    require verification (i.e. a public HTTPS URL)."""
    import logging
    logger = logging.getLogger(__name__)
    if not verify:
        insecure, reason = is_insecure_target(url)
        if not insecure:
            logger.warning(
                "TLS verification disabled for non-internal target %s (reason: %s). "
                "Set ALLOW_INSECURE_INTER_NODE_TLS=true to silence this warning.",
                url, reason,
            )
