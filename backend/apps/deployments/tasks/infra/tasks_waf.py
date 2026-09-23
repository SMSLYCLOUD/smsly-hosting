"""Celery task: converge the open-appsec agent policy from the DB control plane.

Settings (PlatformConfig.openappsec_mode) is the desired state, but the
agent's local_policy.yaml historically converged only during installer
runs — a UI change to 'prevent' sat unapplied (2026-09-23: DB said
prevent while the agent still ran detect-learn). This beat task closes
the loop every 5 minutes: read the desired mode from the DB, read the
live top-level ``mode:`` line from the agent over the Docker API, and
rewrite + restart only on drift.

Fail-open throughout: any Docker/DB error skips the pass and beat
retries. Restarts happen only when the file actually changed. Override
lines (``override-mode``), practices, and operator/SaaS tuning are never
touched — same contract as install.sh ``_harden_openappsec_apply_mode``.
"""

import logging
import re

from celery import shared_task
from django.core.cache import cache

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)

AGENT_CONTAINER = "smsly-appsec-agent"
AGENT_POLICY_PATH = "/etc/cp/conf/local_policy.yaml"
SYNC_LOCK_KEY = "waf-policy-sync-lock"

# Same strict shape the installer matches: exactly a top-level `mode:`
# assignment. Anything else (response-code-only, unknown schema) is left
# untouched — never corrupt the policy.
_MODE_RE = re.compile(
    r"^[ \t]*mode:[ \t]*(detect-learn|prevent)[ \t]*$", re.MULTILINE)


def normalize_desired_mode(raw) -> str:
    """Anything that is not exactly 'prevent' means shadow."""
    return "prevent" if str(raw or "").strip() == "prevent" else "detect-learn"


def extract_plain_mode(policy_text) -> str | None:
    """First top-level mode line, or None for unknown schema."""
    if not policy_text:
        return None
    match = _MODE_RE.search(str(policy_text))
    return match.group(1) if match else None


def _read_agent_policy(container):
    """cat the live policy via the Docker API. Returns text or None."""
    try:
        # No per-call timeout kwarg on exec_run (docker-py 7.x raises
        # TypeError) — the client timeout from from_env() bounds the socket.
        code, out = container.exec_run(["cat", AGENT_POLICY_PATH])
    except Exception as exc:
        logger.debug("WAF sync: policy read failed: %s", exc)
        return None
    if code != 0:
        logger.debug("WAF sync: policy read exit %s", code)
        return None
    try:
        return out.decode("utf-8", errors="replace") if isinstance(out, bytes) else str(out)
    except Exception as exc:
        logger.debug("WAF sync: policy decode failed: %s", exc)
        return None


def _rewrite_agent_mode(container, desired) -> bool:
    """Rewrite top-level mode lines via sed argv (no shell quoting), then
    verify by re-reading. Returns True only when verified."""
    pattern = (
        r"s/^([ \t]*mode:[ \t]*)(detect-learn|prevent)([ \t]*)$"
        r"/\1" + desired + r"\3/"
    )
    try:
        code, _ = container.exec_run(
            ["sed", "-i", "-E", pattern, AGENT_POLICY_PATH])
    except Exception as exc:
        logger.warning("WAF sync: policy rewrite failed: %s", exc)
        return False
    if code != 0:
        logger.warning("WAF sync: policy rewrite exit %s", code)
        return False
    return extract_plain_mode(_read_agent_policy(container)) == desired


@shared_task(
    name="apps.deployments.tasks.infra.tasks_waf.sync_waf_policy_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def sync_waf_policy_task():
    """Converge live agent policy from PlatformConfig.openappsec_mode."""
    if not cache.add(SYNC_LOCK_KEY, "1", timeout=240):
        logger.debug("WAF policy sync skipped — another sync is running")
        return {"ok": True, "skipped": True, "message": "sync already running"}
    try:
        from apps.deployments.models import PlatformConfig

        pc = PlatformConfig.load()
        if not bool(getattr(pc, "openappsec_enabled", True)):
            return {"ok": True, "skipped": True, "message": "open-appsec disabled"}
        desired = normalize_desired_mode(getattr(pc, "openappsec_mode", "detect-learn"))

        try:
            import docker

            client = docker.from_env(timeout=10)
        except Exception as exc:
            logger.debug("WAF sync: docker unavailable: %s", exc)
            return {"ok": True, "skipped": True, "message": "docker unavailable"}
        try:
            container = client.containers.get(AGENT_CONTAINER)
        except Exception:
            logger.debug("WAF sync: agent container absent")
            return {"ok": True, "skipped": True, "message": "agent not running"}

        live_text = _read_agent_policy(container)
        current = extract_plain_mode(live_text)
        if current is None:
            logger.warning("WAF sync: no plain mode line — leaving policy as-is")
            return {"ok": True, "skipped": True, "message": "unknown policy schema"}
        if current == desired:
            return {"ok": True, "changed": False, "mode": current}
        if not _rewrite_agent_mode(container, desired):
            return {"ok": False, "changed": False,
                    "message": "rewrite unverified — policy untouched, will retry"}
        try:
            container.restart(timeout=60)
        except Exception as exc:
            logger.warning("WAF sync: agent restart failed (mode applies on next restart): %s", exc)
            return {"ok": True, "changed": True, "mode": desired,
                    "message": "policy updated; restart failed, applies on next restart"}
        logger.info("WAF policy sync: mode %s -> %s, agent restarted", current, desired)
        return {"ok": True, "changed": True, "previous": current, "mode": desired}
    finally:
        cache.delete(SYNC_LOCK_KEY)
