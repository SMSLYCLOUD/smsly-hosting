"""Cooperative wake hooks for sleepable infra tiers (napd).

napd is a host systemd unit that wakes/sleeps PaaS infra tiers on demand.
The backend reaches it at http://host.docker.internal:8972 (extra_hosts:
host-gateway on backend/worker services) with X-Napd-Secret.

Contract: every function here is warn-only and NEVER raises. A sleeping
tier must degrade to a clear warning + natural downstream failure, never
to a new exception type from the hook itself.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

NAPD_BASE_URL = os.environ.get("NAPD_BASE_URL", "http://host.docker.internal:8972").rstrip("/")
_TIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _napd_secret() -> str:
    try:
        from django.conf import settings
        raw = str(getattr(settings, "NAPD_SHARED_SECRET", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        raw = str(os.environ.get("NAPD_SHARED_SECRET", "") or "").strip()
    return raw


def napd_available() -> bool:
    """True when a napd secret is configured (wake calls can authenticate)."""
    return bool(_napd_secret())


def ensure_tier_awake(tier: str, timeout: int = 120) -> bool:
    """Wake an infra tier if it sleeps, waiting until ready. Never raises.

    Returns True when the tier reports awake, False otherwise (caller
    proceeds and fails naturally with its own error if the tier is down).
    """
    name = str(tier or "").strip().lower()
    if not _TIER_RE.match(name):
        logger.warning("Refusing to wake invalid tier name: %r", tier)
        return False
    secret = _napd_secret()
    if not secret:
        logger.debug("NAPD_SHARED_SECRET unset — skipping wake for tier %r", name)
        return False
    try:
        import requests

        resp = requests.post(
            f"{NAPD_BASE_URL}/wake",
            params={"tier": name, "timeout": max(10, min(int(timeout or 120), 600))},
            headers={"X-Napd-Secret": secret},
            timeout=(5, max(15, min(int(timeout or 120), 600)) + 10),
        )
        if resp.status_code == 200:
            return True
        logger.warning(
            "napd wake for tier %r returned %s: %s",
            name, resp.status_code, (resp.text or "")[:200],
        )
        return False
    except Exception as exc:
        logger.warning("napd wake for tier %r failed (proceeding anyway): %s", name, exc)
        return False
