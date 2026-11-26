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


def _read_host_tier_env(env_path: str = "/opt/smsly-hosting/.env") -> tuple[str, set[str]]:
    """Return (STACK_TIER, set_of_COMPOSE_PROFILES) from env or .env file."""
    stack_tier = str(os.environ.get("STACK_TIER", "") or "").strip().lower()
    raw_profiles = str(os.environ.get("COMPOSE_PROFILES", "") or "").strip()
    if (not stack_tier or not raw_profiles) and env_path and os.path.isfile(env_path):
        try:
            with open(env_path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, _, val = stripped.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key == "STACK_TIER" and not stack_tier:
                        stack_tier = val.lower()
                    elif key == "COMPOSE_PROFILES" and not raw_profiles:
                        raw_profiles = val
        except OSError:
            pass
    profiles = {
        p.strip().lower()
        for p in re.split(r"[,\s]+", raw_profiles)
        if p.strip()
    }
    return stack_tier, profiles


def tier_allowed_on_host(tier: str, env_path: str = "/opt/smsly-hosting/.env") -> bool:
    """True when host STACK_TIER / COMPOSE_PROFILES permit waking *tier*.

    Prevents cooperative wake (e.g. buildcache before Nixpacks builds) from
    starting ~1 GB of optional cache daemons on lite/medium RAM-tiered hosts
    that deliberately excluded build-cache/full profiles.
    """
    name = str(tier or "").strip().lower()
    if not _TIER_RE.match(name):
        return False
    stack_tier, profiles = _read_host_tier_env(env_path)
    if name == "buildcache":
        if stack_tier in ("lite", "medium") and "build-cache" not in profiles and "full" not in profiles:
            return False
        return True
    if name in ("ui", "metrics"):
        if stack_tier == "lite" and "medium" not in profiles and "full" not in profiles:
            return False
        return True
    return True


def _napd_get(path: str, params: dict | None = None, timeout: int = 15):
    """GET napd with auth. Returns parsed JSON, or None. Never raises."""
    secret = _napd_secret()
    if not secret:
        return None
    try:
        import requests

        resp = requests.get(
            f"{NAPD_BASE_URL}{path}", params=params or {},
            headers={"X-Napd-Secret": secret}, timeout=(5, timeout),
        )
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        logger.warning("napd GET %s returned %s", path, resp.status_code)
        return None
    except Exception as exc:
        logger.warning("napd GET %s failed: %s", path, exc)
        return None


def napd_status() -> dict | None:
    """Tier states from napd (/status). None when unavailable. Never raises."""
    try:
        data = _napd_get("/status")
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("napd status failed: %s", exc)
        return None


def sleep_tier(tier: str) -> bool:
    """Ask napd to stop an infra tier. Never raises.

    No ``force`` flag is ever sent: napd itself refuses non-autosleep
    tiers (403) — the daemon stays the authoritative guard. Returns
    True only on napd 200.
    """
    name = str(tier or "").strip().lower()
    if not _TIER_RE.match(name):
        logger.warning("Refusing to sleep invalid tier name: %r", tier)
        return False
    secret = _napd_secret()
    if not secret:
        logger.debug("NAPD_SHARED_SECRET unset — skipping sleep for tier %r", name)
        return False
    try:
        import requests

        resp = requests.post(
            f"{NAPD_BASE_URL}/sleep",
            params={"tier": name},
            headers={"X-Napd-Secret": secret},
            timeout=(5, 30),
        )
        if resp.status_code == 200:
            return True
        logger.warning(
            "napd sleep for tier %r returned %s: %s",
            name, resp.status_code, (resp.text or "")[:200],
        )
        return False
    except Exception as exc:
        try:
            from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
            if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
                raise
        except ImportError:
            pass
        logger.warning("napd sleep for tier %r failed: %s", name, exc)
        return False


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
        # Never swallow Celery's time-limit signals: the hook is warn-only
        # for ITS OWN failures, but a task that hit its time budget must
        # abort, not continue into the tier consumer.
        try:
            from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
            if isinstance(exc, (SoftTimeLimitExceeded, TimeLimitExceeded)):
                raise
        except ImportError:
            pass
        logger.warning("napd wake for tier %r failed (proceeding anyway): %s", name, exc)
        return False
