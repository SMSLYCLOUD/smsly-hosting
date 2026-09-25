"""Post-promotion seed hook for fresh databases.

A service with ``seed_command`` set gets it executed once inside the
live (promoted) container right after promotion. Typical commands:
``python manage.py seed_core``, ``npm run seed``. Runs via the same
docker-exec path as the smoke command. Best-effort: any failure is
logged and stamped, never fails the deploy.
"""
from __future__ import annotations

import logging
import shlex
import subprocess

from django.utils import timezone

logger = logging.getLogger(__name__)

SEED_TIMEOUT = 300


def run_post_promote_seed(deployment, service) -> dict:
    """Run service.seed_command in the live container. Returns a report."""
    command = str(getattr(service, "seed_command", "") or "").strip()
    if not command:
        return {"status": "skipped", "reason": "no seed_command"}
    container_id = (
        getattr(deployment, "container_id", "")
        or getattr(service, "active_runtime_id", "")
    )
    if not container_id:
        logger.warning("Seed skipped for %s: no live container id",
                       getattr(service, "name", "?"))
        return {"status": "skipped", "reason": "no live container"}
    try:
        proc = subprocess.run(
            ["docker", "exec", container_id, "sh", "-c", command],
            capture_output=True, text=True, timeout=SEED_TIMEOUT,
        )
        ok = proc.returncode == 0
        try:
            service.seed_last_run = timezone.now()
            service.save(update_fields=["seed_last_run", "updated_at"])
        except Exception:
            pass
        logger.info("Seed %s for %s (rc=%d): %s",
                    "ok" if ok else "failed",
                    getattr(service, "name", "?"), proc.returncode,
                    ((proc.stdout or "") + (proc.stderr or ""))[-500:])
        return {"status": "ok" if ok else "failed",
                "rc": proc.returncode,
                "tail": ((proc.stdout or "") + (proc.stderr or ""))[-500:]}
    except Exception as exc:
        logger.warning("Seed errored for %s: %s",
                       getattr(service, "name", "?"), exc)
        return {"status": "error", "error": str(exc)[:200]}
