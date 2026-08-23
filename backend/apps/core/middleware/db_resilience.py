"""
Database resilience middleware — rides out transient PostgreSQL failures.

During a Patroni/HAProxy failover window (~30s detection + promotion),
every DB-touching request raises ``django.db.OperationalError``. The
pooler (PgCat) and ``conn_health_checks`` discard stale connections but
never replay the request, so a brief failover turns into a burst of 500s.

This middleware closes that gap with bounded, safe retries:

1. Catches transient connection errors (OperationalError / InterfaceError).
2. Purges stale pooled connections via ``close_old_connections()``.
3. Replays **idempotent requests only** (GET / HEAD / OPTIONS) after a
   short backoff — replaying writes risks duplicate side effects.
4. If recovery fails, or the method is non-idempotent, returns a clean
   503 JSON envelope with ``Retry-After`` instead of an unhandled 500.

Health endpoints are excluded so probes always report true DB state.
"""
import logging
import time

from django.conf import settings
from django.db import InterfaceError, OperationalError, close_old_connections
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Methods safe to replay without duplicate side effects.
_IDEMPOTENT_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

# Probes must reflect real DB status — never retried.
_HEALTH_PATH_PREFIXES = ('/health',)

_TRANSIENT_ERRORS = (OperationalError, InterfaceError)

# Suggested client retry interval when we give up (seconds).
_RETRY_AFTER_SECONDS = 30


class DatabaseResilienceMiddleware:
    """Bounded retry for transient DB connection errors on idempotent requests."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = bool(getattr(settings, 'DB_RESILIENCE_ENABLED', True))
        self.max_retries = int(getattr(settings, 'DB_RESILIENCE_MAX_RETRIES', 1))
        self.retry_delay = float(getattr(settings, 'DB_RESILIENCE_RETRY_DELAY', 0.5))

    def __call__(self, request):
        if not self.enabled or self._is_health_path(request.path):
            return self.get_response(request)
        try:
            return self.get_response(request)
        except _TRANSIENT_ERRORS as exc:
            if request.method.upper() not in _IDEMPOTENT_METHODS or self.max_retries < 1:
                logger.warning(
                    "db_resilience: transient DB error on non-replayable %s %s: %s",
                    request.method, request.path, exc,
                )
                return self._service_unavailable()
            return self._retry(request, exc)

    def _retry(self, request, first_exc):
        last_exc = first_exc
        for attempt in range(1, self.max_retries + 1):
            # Drop every pooled connection — the next query reconnects fresh,
            # which also resets any broken transaction state left by the
            # failed attempt.
            close_old_connections()
            time.sleep(self.retry_delay * attempt)
            try:
                logger.warning(
                    "db_resilience: retrying %s %s after transient DB error "
                    "(attempt %d/%d): %s",
                    request.method, request.path, attempt, self.max_retries,
                    first_exc,
                )
                return self.get_response(request)
            except _TRANSIENT_ERRORS as exc:
                last_exc = exc
        logger.error(
            "db_resilience: DB unavailable after %d retries on %s %s: %s",
            self.max_retries, request.method, request.path, last_exc,
        )
        return self._service_unavailable()

    def _is_health_path(self, path):
        return path.startswith(_HEALTH_PATH_PREFIXES)

    def _service_unavailable(self):
        response = JsonResponse(
            {
                'error': 'Service Unavailable',
                'code': 'database_unavailable',
                'status': 503,
            },
            status=503,
        )
        response['Retry-After'] = str(_RETRY_AFTER_SECONDS)
        return response
