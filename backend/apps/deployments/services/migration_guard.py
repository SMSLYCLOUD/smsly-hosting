"""
Migration guardrails for idempotent errors (Prisma/Django).

Purpose: stop tight failure loops like P3009/P3018 when schema objects already
exist. We mark those migrations as applied and retry once.
"""
from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)


PRISMA_IDEMPOTENT_PATTERNS = (
    "P3009",  # failed migrations exist
    "P3018",  # failed to apply migration
    "already exists",
    "relation .* already exists",
    "table .* already exists",
    "column .* already exists",
)


def prisma_migrate_with_guard(workdir: str, once: bool = True) -> bool:
    """
    Run `prisma migrate deploy`. If idempotent errors occur and ``once`` is
    False, run ``prisma migrate resolve --applied`` on the first pending
    migration and retry. When ``once`` is True (default), return False on
    first failure without retrying.
    Returns True on success.
    """
    try:
        base_cmd = ["npx", "prisma", "migrate", "deploy"]
        first = subprocess.run(base_cmd, cwd=workdir, capture_output=True, text=True)
        if first.returncode == 0:
            return True

        if once:
            logger.warning("Prisma migrate failed (once=True, no retry): %s", (first.stdout + first.stderr).strip()[:500])
            return False

        output = (first.stdout + "\n" + first.stderr).lower()
        if not any(pat.lower() in output for pat in PRISMA_IDEMPOTENT_PATTERNS):
            logger.warning("Prisma migrate failed: %s", output.strip()[:500])
            return False

        # Find the first failed migration name if present
        match = re.search(r"migration\s+`?([0-9a-zA-Z_]+)`?", output)
        migration = match.group(1) if match else None
        if migration:
            resolve_cmd = ["npx", "prisma", "migrate", "resolve", "--applied", migration]
        else:
            resolve_cmd = ["npx", "prisma", "migrate", "resolve", "--applied", "0"]

        res = subprocess.run(resolve_cmd, cwd=workdir, capture_output=True, text=True)
        if res.returncode != 0:
            logger.warning("Prisma resolve failed: %s", (res.stdout + res.stderr).strip()[:500])
            return False

        retry = subprocess.run(base_cmd, cwd=workdir, capture_output=True, text=True)
        if retry.returncode == 0:
            logger.info("Prisma migrate recovered after resolve.")
            return True
        logger.warning("Prisma retry failed: %s", (retry.stdout + retry.stderr).strip()[:500])
        return False
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Prisma guard failed: %s", exc)
        return False
