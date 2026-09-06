"""Shared two-factor helpers for the auth flows.

Three token-issuance paths must ALL respect 2FA enrollment:
  1. password login (ThrottledLoginView)
  2. OAuth session -> token exchange (SessionTokenView)
  3. emergency recovery phrase (recovery_phrase_verify bypasses 2FA
     by design — 128-bit phrase + IP lockout — and issues directly)

This module holds the single source of truth for "does this user need
a TOTP step" plus the session keys and attempt cap, so the three
views cannot drift apart again.
"""
import logging

logger = logging.getLogger(__name__)

# Session keys for the pending 2FA handshake (set by step 1, consumed
# by step 2 at POST /api/v1/auth/2fa/login/).
SESSION_2FA_USER_ID = "2fa_user_id"
SESSION_2FA_ATTEMPTS = "2fa_attempts"

# Max wrong TOTP codes per pending handshake. A 6-digit TOTP has ~1M
# codes with ~3 valid per 90s window (tolerance=1); 10 guesses per
# session makes brute force infeasible, on top of the 10/min/IP
# TwoFactorLoginRateThrottle.
MAX_2FA_ATTEMPTS_PER_SESSION = 10


def user_requires_2fa(user) -> bool:
    """True when the user has at least one CONFIRMED TOTP device."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    try:
        from django_otp import user_has_device

        return bool(user_has_device(user, confirmed=True))
    except Exception as exc:
        # Never fail OPEN on infrastructure errors here — but also
        # never lock everyone out: log loudly and report "no device"
        # so login keeps working; the missing-device error will show
        # up in monitoring instead of silently disabling 2FA gating.
        logger.error("2FA device check failed for %s: %s", user, exc)
        return False


def stash_pending_2fa(request, user) -> None:
    """Record a pending 2FA handshake in the session (step 1)."""
    request.session[SESSION_2FA_USER_ID] = str(user.pk)
    request.session[SESSION_2FA_ATTEMPTS] = 0


def consume_pending_2fa(request):
    """Return (user_id, attempts) or (None, 0) when no handshake pending."""
    user_id = request.session.get(SESSION_2FA_USER_ID)
    try:
        attempts = int(request.session.get(SESSION_2FA_ATTEMPTS, 0) or 0)
    except (TypeError, ValueError):
        attempts = 0
    return user_id, attempts


def bump_pending_2fa_attempts(request) -> int:
    """Increment the per-session wrong-code counter; returns new count."""
    _user_id, attempts = consume_pending_2fa(request)
    attempts += 1
    request.session[SESSION_2FA_ATTEMPTS] = attempts
    return attempts


def clear_pending_2fa(request) -> None:
    """Drop the pending handshake (success, lockout, or restart)."""
    request.session.pop(SESSION_2FA_USER_ID, None)
    request.session.pop(SESSION_2FA_ATTEMPTS, None)
