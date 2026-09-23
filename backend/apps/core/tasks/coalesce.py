"""Run-coalescing for hot periodic Celery tasks (Redis assist layer).

Beat dispatches on cadence, but nothing stops a slow run from
overlapping the next tick — overlapping runs piled host load to 178 on
2026-09-16. Wrapping a periodic task with ``@skip_if_recent`` makes
repeats within the window a no-op via an atomic cache add (Redis in
prod; LocMem per-process when degraded — still correct, just local).

Rules:
- TTL should equal the task's soft time limit: long enough that a
  still-running pass always suppresses its overlap, short enough that a
  crashed pass only suppresses a bounded number of ticks.
- Never wrap liveness-critical tasks (heartbeats, election, failover
  watchdogs): a skipped tick there delays outage detection.
- The skip result is a plain dict so result backends store it cleanly.
- On cache errors the task runs (fail-open: monitoring must not go
  blind because Redis hiccuped).
"""
import functools
import logging

logger = logging.getLogger(__name__)


def skip_if_recent(key, ttl_seconds):
    """Skip this run if the same task ran within the last ttl_seconds."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                from django.core.cache import cache
                claimed = cache.add(key, 1, ttl_seconds)
            except Exception as exc:
                logger.debug("coalesce check skipped (%s); running", exc)
                return fn(*args, **kwargs)
            if not claimed:
                logger.debug("coalesced periodic run: %s", key)
                return {"status": "skipped", "reason": "coalesced", "key": key}
            return fn(*args, **kwargs)
        return wrapper
    return decorator
