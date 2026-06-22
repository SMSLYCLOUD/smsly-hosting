"""
HMAC authentication for the lite-agent registrar.

The lite-agent's registrar (a small service in the agent's
docker-compose stack) needs to call master endpoints
``/api/v1/servers/{id}/agent-ready/`` and
``/api/v1/servers/{id}/agent-heartbeat/`` to report its own
state. The endpoints must accept requests from the agent
**without an operator session** so the agent can self-register
before anyone has logged in.

Why a per-server secret and not the global GATEWAY_SECRET?

* Each lite agent is provisioned with its own ``gateway_secret``
  generated in ``services/provisioner.build_agent_lite_install_env``
  (a 32-byte hex token). This isolates blast radius: compromise
  of one agent does not leak credentials for all other agents.
* The global GATEWAY_SECRET is reserved for legacy full-stack
  nodes that don't have a per-server secret.

Why not ``ZeroTrustHMACAuthentication``?

* That authentication class uses the **global** ``GATEWAY_SECRET``
  and authenticates as the first superuser — useful for node-to-node
  service traffic but wrong for per-agent attestation.
* This module verifies the per-server ``gateway_secret`` and
  returns ``True``/``False`` so the calling view can act on the
  result without impersonating a specific user.

The signed payload format matches the rest of the platform:

    {method}|{full_path}|{timestamp}|{nonce}|{body_sha256}

with timestamp freshness of 60s and a nonce-replay cache of 120s.
"""
import hashlib
import hmac
import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)


AGENT_HMAC_TIMESTAMP_TOLERANCE = 60
AGENT_HMAC_NONCE_TTL_SECONDS = 120


def _client_ip(request):
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "").split(",")[0].strip()
    return forwarded or str(request.META.get("REMOTE_ADDR", "unknown") or "unknown").strip() or "unknown"


def compute_agent_hmac(secret, method, full_path, timestamp, nonce, body):
    """
    Build the canonical HMAC payload for an agent-registrar call.

    Returns the hex digest. Inputs:
        secret:    the per-agent gateway_secret
        method:    HTTP method (uppercase)
        full_path: request.get_full_path() (preserves query string)
        timestamp: integer unix timestamp as string
        nonce:     a per-request random string
        body:      raw request body bytes
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    body_hash = hashlib.sha256(body or b"").hexdigest()
    payload = f"{method.upper()}|{full_path}|{timestamp}|{nonce}|{body_hash}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_agent_hmac(request, server) -> bool:
    """
    Verify the request carries a valid per-server HMAC V2 signature.

    Returns True on success, False on any failure. Failures are
    logged at WARNING with the source IP but never raise — the
    caller is responsible for converting the False to a 401.
    """
    if server is None:
        return False
    secret = str(getattr(server, "gateway_secret", "") or "").strip()
    if not secret:
        logger.warning(
            "agent_hmac: server %s has no gateway_secret configured",
            getattr(server, "id", "?"),
        )
        return False

    signature = str(request.headers.get("X-Gateway-Signature-V2", "") or "").strip()
    timestamp = str(request.headers.get("X-Request-Timestamp", "") or "").strip()
    nonce = str(request.headers.get("X-Request-Nonce", "") or "").strip()

    if not signature or not timestamp or not nonce:
        logger.warning(
            "agent_hmac: missing headers from %s (sig=%s ts=%s nonce=%s)",
            _client_ip(request), bool(signature), bool(timestamp), bool(nonce),
        )
        return False

    try:
        req_ts = int(timestamp)
    except (TypeError, ValueError):
        logger.warning("agent_hmac: invalid timestamp %r", timestamp)
        return False
    if abs(int(time.time()) - req_ts) > AGENT_HMAC_TIMESTAMP_TOLERANCE:
        logger.warning("agent_hmac: timestamp expired (delta=%d)", int(time.time()) - req_ts)
        return False

    nonce_key = f"agent_hmac_nonce:{nonce}"
    if cache.get(nonce_key):
        logger.warning("agent_hmac: nonce replay attempt from %s", _client_ip(request))
        return False
    try:
        cache.set(nonce_key, "1", timeout=AGENT_HMAC_NONCE_TTL_SECONDS)
    except Exception as exc:
        # If the cache backend is down, fail closed — better to reject
        # a legitimate agent than accept a replayed request.
        logger.warning("agent_hmac: nonce cache write failed, failing closed: %s", exc)
        return False

    try:
        body = request.body
    except Exception:
        body = b""
    expected = compute_agent_hmac(
        secret,
        request.method,
        request.get_full_path(),
        timestamp,
        nonce,
        body,
    )
    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "agent_hmac: signature mismatch for server %s from %s",
            getattr(server, "id", "?"), _client_ip(request),
        )
        return False
    return True
