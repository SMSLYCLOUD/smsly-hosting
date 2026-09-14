"""Periodic CrowdSec hygiene: configurable automatic unblocking."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)

FIRST_STRIKE_SCENARIOS: dict[str, int] = {
    # Hub scenario name -> pinned leaky-bucket capacity. A single hit on
    # any of these is certain malice (no legit client requests
    # /.git/config, ../.. payloads, or phpmyadmin scans), so the bucket
    # must overflow on the FIRST distinct event instead of serving N
    # free probes while it fills (http-sensitive-files served 4).
    #
    # Deliberately EXCLUDED (first hit is NOT proof of malice):
    #   http-probing / http-crawl-non_statics (typos, favicon, legit crawlers),
    #   http-generic-bf + 401/403-bf (one failed login must never ban),
    #   http-bad-user-agent (legit scripts).
    # Already first-strike upstream, no override needed:
    #   http-backdoors-attempts (capacity 1), http-cve-probing (trigger type).
    "crowdsecurity/http-sensitive-files": 1,
    "crowdsecurity/http-admin-interface-probing": 1,
    "crowdsecurity/http-path-traversal-probing": 1,
}

_FIRST_STRIKE_SCENARIOS_DIR = "/etc/crowdsec/scenarios"


def _first_strike_slug(hub_name: str) -> str:
    base = hub_name.split("/", 1)[1] if "/" in hub_name else hub_name
    return f"{base}-first-strike"


def _first_strike_scenario_name(hub_name: str) -> str:
    return f"smsly/{_first_strike_slug(hub_name)}"


def _first_strike_filename(hub_name: str) -> str:
    return f"{_first_strike_slug(hub_name)}.yaml"


def _pin_capacity(yaml_text: str, capacity: int) -> str | None:
    """Pin the top-level bucket capacity; None when no capacity line exists."""
    if not re.search(r"(?m)^capacity:\s*\d+\s*$", yaml_text):
        return None
    return re.sub(
        r"(?m)^capacity:\s*\d+\s*$", f"capacity: {capacity}", yaml_text, count=1
    )


def _first_strike_config() -> bool:
    """Whether first-strike overrides should be enforced (default on)."""
    try:
        from apps.deployments.models import PlatformConfig

        return bool(
            getattr(PlatformConfig.load(), "crowdsec_first_strike_enabled", True)
        )
    except Exception as exc:
        logger.debug("CrowdSec first-strike config unreadable: %s", exc)
        return True


def _auto_unblock_config() -> tuple[bool, int]:
    """Return (enabled, max_age_hours) with fail-safe defaults."""
    try:
        from apps.deployments.models import PlatformConfig

        config = PlatformConfig.load()
        enabled = getattr(config, "crowdsec_auto_unblock_enabled", True)
        hours = getattr(config, "crowdsec_auto_unblock_after_hours", 24)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 24
        if hours < 1:
            hours = 1
        return bool(enabled), hours
    except Exception as exc:
        logger.debug("CrowdSec auto-unblock config unreadable: %s", exc)
        return True, 24


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    name="apps.crowdsec.tasks.crowdsec_auto_unblock",
)
def crowdsec_auto_unblock():
    """Remove bans older than the configured retention window.

    CrowdSec scenario bans carry their own durations, but alert-only
    records, LAPI leftovers, and stuck decisions can linger past any
    sane lifetime. This sweeper deletes Ip/Range bans whose observed
    start is older than ``crowdsec_auto_unblock_after_hours`` — unless
    the operator disabled automatic unblocking, in which case every ban
    requires a manual Unblock click. Simulated decisions are never
    touched. Returns counters for logging/alerting.
    """
    from .services import _parse_dt, get_crowdsec_service

    enabled, max_age_hours = _auto_unblock_config()
    if not enabled:
        return {"status": "ok", "mode": "disabled", "unbanned": 0}

    try:
        service = get_crowdsec_service()
        decisions = service.get_decisions(active=False, limit=500)
    except Exception as exc:
        logger.error("CrowdSec auto-unblock: decision fetch failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    now = datetime.now(timezone.utc)
    unbanned: list[str] = []
    failed: list[str] = []
    skipped = 0
    for decision in decisions:
        try:
            if decision.simulated:
                skipped += 1
                continue
            scope = (decision.scope or "").strip().lower()
            if scope == "ip":
                range_type = "Ip"
            elif scope == "range":
                range_type = "Range"
            else:
                # Unknown scope — never auto-delete what we can't classify.
                skipped += 1
                continue
            if not decision.value:
                skipped += 1
                continue
            start = _parse_dt(decision.start_time) or _parse_dt(
                decision.first_seen
            )
            if start is None:
                # No trustworthy age — leave for manual review.
                skipped += 1
                continue
            age_hours = (now - start).total_seconds() / 3600.0
            if age_hours < max_age_hours:
                skipped += 1
                continue
            result = service.unban(decision.value, range_type)
            if "error" in result:
                failed.append(decision.value)
                logger.warning(
                    "CrowdSec auto-unblock: failed to remove %s: %s",
                    decision.value, result["error"],
                )
            else:
                unbanned.append(decision.value)
                logger.info(
                    "CrowdSec auto-unblock: removed %s ban for %s (age %.1fh)",
                    decision.type or decision.scope, decision.value, age_hours,
                )
        except Exception:
            logger.exception(
                "CrowdSec auto-unblock failed for decision %s",
                getattr(decision, "id", "?"),
            )
            failed.append(getattr(decision, "value", "?") or "?")

    return {
        "status": "ok",
        "mode": "enabled",
        "unbanned": len(unbanned),
        "failed": len(failed),
        "skipped": skipped,
        "sample": unbanned[:10],
    }


def _docker(*args: str, timeout: int = 60):
    """Run a docker CLI command from the backend container."""
    import subprocess

    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def _crowdsec_hub_path(hub_name: str) -> str:
    return f"/etc/crowdsec/hub/scenarios/{hub_name}.yaml"


def _list_hub_scenarios() -> dict[str, str]:
    """Map installed hub scenario name -> status (best effort)."""
    import json

    result = _docker(
        "exec", "smsly-crowdsec", "cscli", "scenarios", "list", "-o", "json",
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cscli scenarios list failed: {result.stderr[:200]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError(f"cscli scenarios list unparsable: {exc}")
    items = payload.get("scenarios") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return {}
    out: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"])] = str(item.get("status", "enabled"))
    return out


def _list_override_files() -> set[str]:
    result = _docker(
        "exec", "smsly-crowdsec", "ls", _FIRST_STRIKE_SCENARIOS_DIR, timeout=30
    )
    if result.returncode != 0:
        return set()
    return {
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip().endswith("-first-strike.yaml")
    }


def _read_container_file(path: str) -> str | None:
    result = _docker("exec", "smsly-crowdsec", "cat", path, timeout=30)
    if result.returncode != 0:
        return None
    return result.stdout or ""


def _write_override_file(filename: str, content: str) -> bool:
    import os
    import tempfile

    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as handle:
            handle.write(content)
            tmp = handle.name
        # Backend runs as uid 1000: without this the copied file is
        # 0600/uid-1000, which the CrowdSec container cannot read
        # (user-namespace mapping) — config test then fails with
        # "permission denied" (2026-09-14 live incident). World-readable
        # content is fine: scenario files carry no secrets.
        os.chmod(tmp, 0o644)
        result = _docker(
            "cp", tmp,
            f"smsly-crowdsec:{_FIRST_STRIKE_SCENARIOS_DIR}/{filename}",
            timeout=60,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.warning("CrowdSec first-strike: file write failed: %s", exc)
        return False
    finally:
        try:
            if tmp:
                os.unlink(tmp)
        except OSError:
            pass


def _crowdsec_config_test() -> bool:
    result = _docker(
        "exec", "smsly-crowdsec", "crowdsec", "-t",
        "-c", "/etc/crowdsec/config.yaml", timeout=120,
    )
    if result.returncode != 0:
        logger.error(
            "CrowdSec first-strike: config test failed, NOT reloading: %s",
            (result.stderr or result.stdout or "")[-500:],
        )
        return False
    return True


def _crowdsec_reload() -> bool:
    result = _docker(
        "kill", "--signal=HUP", "smsly-crowdsec", timeout=30
    )
    if result.returncode != 0:
        logger.error(
            "CrowdSec first-strike: reload signal failed: %s",
            (result.stderr or "")[:200],
        )
        return False
    return True


def _build_override_content(hub_name: str, hub_yaml: str, capacity: int) -> str | None:
    pinned = _pin_capacity(hub_yaml, capacity)
    if pinned is None:
        logger.warning(
            "CrowdSec first-strike: hub file for %s has no capacity line — "
            "leaving hub behavior intact",
            hub_name,
        )
        return None
    header = (
        "# SMSLY first-strike override — managed by "
        "crowdsec_first_strike_sync. DO NOT EDIT.\n"
        f"# Source: {hub_name} (bucket capacity pinned to {capacity}; "
        "filters/data track the hub file and are re-derived on every sync).\n"
    )
    return header + pinned


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    name="apps.crowdsec.tasks.crowdsec_first_strike_sync",
)
def crowdsec_first_strike_sync():
    """Enforce first-strike (capacity-1) overrides for exploit scenarios.

    Derives each override from the CURRENT hub file (capacity line pinned
    to 1), installs it as ``smsly/<slug>-first-strike``, and removes the
    original hub scenario so only the harsh copy loads. When disabled,
    removes the overrides and reinstalls the hub originals. Reloads
    CrowdSec only when something actually changed (after a config test).
    Idempotent: a converged host is a cheap no-op.
    """
    try:
        enabled = _first_strike_config()
        installed = _list_hub_scenarios()
        overrides = _list_override_files()
    except Exception as exc:
        logger.error("CrowdSec first-strike sync: discovery failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    changed = False
    applied: list[str] = []
    skipped: list[str] = []
    try:
        for hub_name, capacity in FIRST_STRIKE_SCENARIOS.items():
            filename = _first_strike_filename(hub_name)
            fs_name = _first_strike_scenario_name(hub_name)
            if enabled:
                hub_yaml = _read_container_file(_crowdsec_hub_path(hub_name))
                if hub_yaml is None:
                    logger.warning(
                        "CrowdSec first-strike: hub file missing for %s — skipping",
                        hub_name,
                    )
                    skipped.append(hub_name)
                    continue
                desired = _build_override_content(hub_name, hub_yaml, capacity)
                if desired is None:
                    skipped.append(hub_name)
                    continue
                if filename not in overrides:
                    current = None
                else:
                    current = _read_container_file(
                        f"{_FIRST_STRIKE_SCENARIOS_DIR}/{filename}"
                    )
                if current != desired:
                    if _write_override_file(filename, desired):
                        changed = True
                        applied.append(fs_name)
                    else:
                        skipped.append(hub_name)
                        continue
                status = installed.get(hub_name, "enabled")
                if status != "disabled" and hub_name in installed:
                    remove = _docker(
                        "exec", "smsly-crowdsec", "cscli", "scenarios",
                        "remove", hub_name, timeout=60,
                    )
                    if remove.returncode == 0:
                        changed = True
                        applied.append(f"removed:{hub_name}")
                    else:
                        logger.warning(
                            "CrowdSec first-strike: could not remove %s: %s",
                            hub_name, (remove.stderr or "")[:200],
                        )
            else:
                if filename in overrides:
                    delete = _docker(
                        "exec", "smsly-crowdsec", "rm",
                        f"{_FIRST_STRIKE_SCENARIOS_DIR}/{filename}",
                        timeout=30,
                    )
                    if delete.returncode == 0:
                        changed = True
                        applied.append(f"deleted:{fs_name}")
                if hub_name not in installed:
                    install = _docker(
                        "exec", "smsly-crowdsec", "cscli", "scenarios",
                        "install", hub_name, timeout=120,
                    )
                    if install.returncode == 0:
                        changed = True
                        applied.append(f"restored:{hub_name}")
        reloaded = False
        if changed:
            if _crowdsec_config_test():
                reloaded = _crowdsec_reload()
            else:
                return {
                    "status": "error",
                    "reason": "config test failed; overrides staged but NOT reloaded",
                    "applied": applied,
                }
        return {
            "status": "ok",
            "mode": "enabled" if enabled else "disabled",
            "changed": changed,
            "reloaded": reloaded,
            "applied": applied,
            "skipped": skipped,
        }
    except Exception as exc:
        logger.exception("CrowdSec first-strike sync failed")
        return {"status": "error", "reason": str(exc)}
