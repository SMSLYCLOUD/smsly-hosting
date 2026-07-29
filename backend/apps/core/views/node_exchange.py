"""
Node-to-node token exchange endpoint.

Allows a newly connected/provisioned server to request an API token
for inter-node communication. This is called by the SOURCE server
when it first connects to a TARGET server.

Flow:
1. Source authenticates with admin credentials (username/password)
2. Target generates a fresh smsly_ API token
3. Source stores that token in ManagedServer.api_token

This replaces the manual copy-paste workflow entirely.

SECURITY: Both endpoints require an HMAC signature over the request
(method|path|ts|nonce|body_hash) using the platform's GATEWAY_SECRET.
Only already-provisioned nodes (which know the secret) can call these
endpoints — the username/password path on the legacy endpoint is no
longer sufficient on its own.
"""

import logging

from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.deployments.models.api_token import APIToken
from apps.core.rate_limiting import NodeTokenExchangeThrottle
from apps.core.views.transfer import _verify_transfer_sync_hmac

logger = logging.getLogger(__name__)


def _client_ip(request):
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", "") or "").split(",")[0].strip()
    return forwarded or str(request.META.get("REMOTE_ADDR", "unknown") or "unknown").strip() or "unknown"


def _audit_node_token_attempt(username, client_ip, *, success, reason):
    try:
        from apps.core.models.audit import AuditLog
        AuditLog(
            actor=str(username or "unknown")[:255],
            action='NODE_TOKEN_EXCHANGE_ATTEMPT',
            target=f'ip={client_ip}'[:255],
            metadata={'success': bool(success), 'reason': str(reason)[:255]},
        ).save()
    except Exception as exc:
        logger.warning("Failed to write NODE_TOKEN_EXCHANGE_ATTEMPT audit log: %s", exc)


def _clean_node_name(value):
    node_name = str(value or "Remote Node").strip()
    return node_name[:100] or "Remote Node"


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([NodeTokenExchangeThrottle])
def node_token_exchange(request):
    """
    Exchange admin credentials for an API token.

    POST /api/v1/auth/node-token-exchange/
    Headers: X-Gateway-Signature-V2, X-Request-Timestamp, X-Request-Nonce
    Body: { "username": "admin", "password": "xxx", "node_name": "Primary .62" }

    Returns: { "token": "smsly_abc123...", "prefix": "smsly_abc123" }

    SECURITY: The HMAC signature is REQUIRED. Only already-provisioned
    nodes (which know GATEWAY_SECRET) can call this endpoint. The
    signature covers method|path|ts|nonce|body_hash. Brute-forcing the
    admin password is no longer viable at any practical rate.
    """
    raw_body = request.body
    client_ip = _client_ip(request)
    username_hint = ""
    try:
        username_hint = str(request.data.get("username", "") or "")
    except Exception:
        username_hint = ""

    if not _verify_transfer_sync_hmac(request, client_ip, raw_body):
        _audit_node_token_attempt(
            username_hint, client_ip, success=False, reason='invalid_or_missing_hmac',
        )
        logger.warning(
            "Node token exchange rejected: invalid HMAC for username '%s' from %s",
            username_hint, client_ip,
        )
        return Response(
            {"error": "Valid HMAC signature is required. Sign this request with GATEWAY_SECRET or use the node-token-exchange-hmac endpoint."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    username = username_hint.strip()
    password = request.data.get("password", "").strip()
    node_name = _clean_node_name(request.data.get("node_name", "Remote Node"))

    if not username or not password:
        _audit_node_token_attempt(
            username, client_ip, success=False, reason='missing_credentials',
        )
        return Response(
            {"error": "username and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        _audit_node_token_attempt(
            username, client_ip, success=False, reason='invalid_credentials',
        )
        logger.warning("Node token exchange failed: invalid credentials for '%s'", username)
        return Response(
            {"error": "Invalid credentials."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_staff:
        _audit_node_token_attempt(
            username, client_ip, success=False, reason='not_staff',
        )
        return Response(
            {"error": "Only admin users can request node tokens."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Check if a token already exists for this node name
    existing = APIToken.objects.filter(
        user=user,
        name=f"node:{node_name}",
        is_active=True,
    ).first()

    if existing:
        # Revoke old one and create fresh (tokens can't be recovered)
        existing.is_active = False
        existing.save(update_fields=["is_active"])

    token_instance, raw_token = APIToken.create_token(user, name=f"node:{node_name}")

    _audit_node_token_attempt(
        username, client_ip, success=True, reason='ok',
    )
    logger.info("Node token exchange: created token for node '%s' (user: %s)", node_name, username)

    return Response({
        "token": raw_token,
        "prefix": token_instance.prefix,
        "user": username,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([NodeTokenExchangeThrottle])
def node_token_exchange_via_gateway(request):
    """
    Exchange a GATEWAY_SECRET (HMAC) for an API token.

    POST /api/v1/auth/node-token-exchange-hmac/
    Body: { "node_name": "Remote Node" }
    Headers: X-Gateway-Signature-V2, X-Request-Timestamp

    This is the zero-credential path: if the remote server knows the
    GATEWAY_SECRET of this server, it can request a token without
    username/password.
    """
    import hashlib
    import hmac
    import time

    from django.conf import settings

    signature = request.headers.get("X-Gateway-Signature-V2", "")
    timestamp = request.headers.get("X-Request-Timestamp", "")
    nonce = request.headers.get("X-Request-Nonce", "")
    raw_body = request.body
    node_name = _clean_node_name(request.data.get("node_name", "Remote Node"))

    if not signature or not timestamp or not nonce:
        return Response(
            {"error": "HMAC signature, timestamp, and nonce headers are required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Verify timestamp freshness
    try:
        req_ts = int(timestamp)
        if abs(int(time.time()) - req_ts) > 15:
            return Response({"error": "Timestamp expired."}, status=status.HTTP_401_UNAUTHORIZED)
    except ValueError:
        return Response({"error": "Invalid timestamp."}, status=status.HTTP_400_BAD_REQUEST)

    # SECURITY: nonce replay protection. Each nonce can be used once
    # within the freshness window. Without this, a captured request
    # can be replayed for up to 15s.
    from django.core.cache import cache
    nonce_key = f"node_token_nonce:{nonce}"
    if cache.get(nonce_key):
        return Response({"error": "Nonce already used."}, status=status.HTTP_401_UNAUTHORIZED)
    cache.set(nonce_key, "1", timeout=30)

    # Verify HMAC
    gw_secret = str(getattr(settings, "GATEWAY_SECRET", "") or settings.SECRET_KEY or "").strip()
    if not gw_secret:
        return Response({"error": "Gateway secret is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    method = request.method
    path = request.get_full_path()
    body_hash = hashlib.sha256(raw_body).hexdigest()
    # Bind the nonce into the signed payload so a captured request
    # cannot be replayed with a fresh nonce.
    payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
    expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return Response({"error": "Invalid HMAC signature."}, status=status.HTTP_403_FORBIDDEN)

    # HMAC valid — issue token for the first superuser
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True, is_active=True).first()
    if not admin:
        return Response({"error": "No admin user on this node."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Revoke old node token if exists
    APIToken.objects.filter(user=admin, name=f"node:{node_name}", is_active=True).update(is_active=False)
    token_instance, raw_token = APIToken.create_token(admin, name=f"node:{node_name}")

    logger.info("HMAC node token exchange: created token for node '%s'", node_name)

    return Response({
        "token": raw_token,
        "prefix": token_instance.prefix,
        "user": admin.username,
    })
