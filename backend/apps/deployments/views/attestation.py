"""
Server identity attestation views.

Provides challenge-response protocol for verifying server identity
before allowing cluster operations (mesh join, transfers, etc.).

Protocol:
1. Verifier POSTs to /api/v1/internal/attest/challenge/ with {target_wg_address}
2. Target responds with {challenge: <random_nonce>, expires_at: <timestamp>}
3. Verifier sends challenge to target over a trusted channel (SSH or existing HMAC)
4. Target signs the nonce with its gateway_secret and returns {signature: <hex>}
5. Verifier checks signature against the target's stored gateway_secret

SEC-ZT-004: Prevents server impersonation by requiring proof of possession
of the gateway_secret associated with the claimed wg_address.
"""

import hashlib
import hmac as hmac_mod
import logging
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response

from apps.core.rate_limiting import (
    AttestationVerifyRateThrottle,
    NodeTokenExchangeThrottle,
)

logger = logging.getLogger(__name__)

# Nonce expiration (seconds)
CHALLENGE_TTL = 120
# Cache prefix
CHALLENGE_CACHE_PREFIX = "attest_challenge_"
# Max outstanding nonces before the challenge endpoint returns 503
CHALLENGE_CACHE_MAX_ENTRIES = 100
# Cache key for the challenge count counter
CHALLENGE_COUNT_CACHE_KEY = "attest_challenge_count"


@api_view(["POST"])
@throttle_classes([NodeTokenExchangeThrottle])  # 5/min per source IP
def attestation_challenge(request):
    """
    Request an attestation challenge for a target WireGuard address.

    The verifier should send this to the target server to prove identity.
    Returns a random nonce that must be signed with the target's gateway_secret.
    """
    target_wg_address = request.data.get("target_wg_address", "").strip()
    if not target_wg_address:
        return Response(
            {"error": "target_wg_address is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    nonce = secrets.token_hex(32)
    expires_at = int(time.time()) + CHALLENGE_TTL
    cache_key = f"{CHALLENGE_CACHE_PREFIX}{nonce}"

    # Store the nonce -> wg_address mapping
    cache.set(cache_key, target_wg_address, timeout=CHALLENGE_TTL)

    logger.info("Issued attestation challenge for %s (nonce: %s...)", target_wg_address, nonce[:16])

    return Response({
        "challenge": nonce,
        "expires_at": expires_at,
        "ttl_seconds": CHALLENGE_TTL,
    })


@api_view(["POST"])
@throttle_classes([AttestationVerifyRateThrottle])  # 30/min per source IP
def attestation_verify(request):
    """
    Verify an attestation response.

    The target server should:
    1. Receive the challenge nonce
    2. Sign it with its gateway_secret using HMAC-SHA256
    3. Return the signature

    This endpoint checks the signature against the stored gateway_secret
    for the wg_address associated with the nonce.
    """
    nonce = request.data.get("challenge", "").strip()
    signature = request.data.get("signature", "").strip()
    sender_wg = request.data.get("sender_wg_address", "").strip()

    if not nonce or not signature or not sender_wg:
        return Response(
            {"error": "challenge, signature, and sender_wg_address are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Look up the nonce
    cache_key = f"{CHALLENGE_CACHE_PREFIX}{nonce}"
    expected_wg = cache.get(cache_key)
    if not expected_wg:
        return Response(
            {"error": "Challenge expired or not found"},
            status=status.HTTP_410_GONE,
        )

    if expected_wg != sender_wg:
        return Response(
            {"error": "sender_wg_address does not match challenge target"},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Look up the peer's gateway_secret
    from ..models.mesh import WireGuardPeer
    # SECURITY: don't distinguish "DB error" from "unknown peer" in
    # the response — that would let a network-adjacent attacker probe
    # for DB failures. Both surface as 404.
    try:
        peer = WireGuardPeer.objects.select_related("server").filter(
            wg_address=sender_wg, is_active=True,
        ).first()
    except Exception:
        logger.exception("DB error resolving peer for attestation: sender_wg=%s", sender_wg)
        peer = None

    if not peer or not peer.server:
        return Response(
            {"error": "Unknown peer wg_address"},
            status=status.HTTP_404_NOT_FOUND,
        )

    gateway_secret = str(getattr(peer.server, "gateway_secret", "") or "").strip()
    if not gateway_secret:
        # Fallback to global secret
        gateway_secret = str(getattr(settings, "GATEWAY_SECRET", "")).strip()
    if not gateway_secret:
        return Response(
            {"error": "No gateway_secret configured for peer"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # Verify the signature
    expected_sig = hmac_mod.new(
        gateway_secret.encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac_mod.compare_digest(signature, expected_sig):
        logger.warning("Attestation failed for %s: signature mismatch", sender_wg)
        return Response(
            {"verified": False, "error": "Signature mismatch"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Invalidate the nonce (single use)
    cache.delete(cache_key)

    logger.info("Attestation verified for %s", sender_wg)
    return Response({
        "verified": True,
        "wg_address": sender_wg,
    })
