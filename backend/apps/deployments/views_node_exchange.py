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
"""

import logging

from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.deployments.api_token_auth import APIToken

logger = logging.getLogger(__name__)


@api_view(["POST"])
@permission_classes([AllowAny])
def node_token_exchange(request):
    """
    Exchange admin credentials for an API token.

    POST /api/v1/auth/node-token-exchange/
    Body: { "username": "admin", "password": "xxx", "node_name": "Primary .62" }

    Returns: { "token": "smsly_abc123...", "prefix": "smsly_abc123" }

    This endpoint is AllowAny because the caller doesn't have a token yet —
    that's the entire point. It uses username/password to bootstrap trust.
    """
    username = request.data.get("username", "").strip()
    password = request.data.get("password", "").strip()
    node_name = request.data.get("node_name", "Remote Node").strip()

    if not username or not password:
        return Response(
            {"error": "username and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        logger.warning("Node token exchange failed: invalid credentials for '%s'", username)
        return Response(
            {"error": "Invalid credentials."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_staff:
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

    logger.info("Node token exchange: created token for node '%s' (user: %s)", node_name, username)

    return Response({
        "token": raw_token,
        "prefix": token_instance.prefix,
        "user": username,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
def node_token_exchange_via_gateway(request):
    """
    Exchange a GATEWAY_SECRET (HMAC) for an API token.

    POST /api/v1/auth/node-token-exchange-hmac/
    Body: { "node_name": "Primary .62" }
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
    raw_body = request.body
    node_name = request.data.get("node_name", "Remote Node").strip()

    if not signature or not timestamp:
        return Response(
            {"error": "HMAC signature headers required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Verify timestamp freshness
    try:
        req_ts = int(timestamp)
        if abs(int(time.time()) - req_ts) > 300:
            return Response({"error": "Timestamp expired."}, status=status.HTTP_401_UNAUTHORIZED)
    except ValueError:
        return Response({"error": "Invalid timestamp."}, status=status.HTTP_400_BAD_REQUEST)

    # Verify HMAC
    gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
    method = request.method
    path = request.get_full_path()
    body_hash = hashlib.sha256(raw_body).hexdigest()
    payload = f"{method}|{path}|{timestamp}|{body_hash}"
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
