"""
DRF custom exception handler.

Goals:
1. Always return a stable JSON shape: ``{"error": ..., "detail": ..., "code": ...}``
   so frontend toasts and ops dashboards can branch on ``code``.
2. Log the offending request body + serializer errors at WARNING level.
   The default Django logging only emits ``Bad Request: /api/v1/services/``
   which is useless for diagnosing real client bugs.

Imported via ``REST_FRAMEWORK['EXCEPTION_HANDLER']`` in settings.py.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("smsly.api.errors")


# Fields we redact from request bodies before logging.
# Keep this list short — only secrets, never user-controlled keys.
_REDACT_KEYS = {
    "password", "current_password", "new_password", "token", "api_key",
    "secret", "webhook_secret", "private_key", "ssh_key", "key_material",
}


def _safe_body_for_log(request) -> str:
    """Return a redacted, length-capped string of the request body.

    Multi-part / file uploads are skipped — those can be megabytes and
    would drown out the actual diagnostic value of the log line.
    """
    try:
        ctype = (request.content_type or "").lower()
        if "multipart/form-data" in ctype or "application/octet-stream" in ctype:
            return f"<{ctype} skipped for log>"
        raw = request.body
        if not raw:
            return "<empty body>"
        if len(raw) > 4096:
            return f"<body too large: {len(raw)} bytes>"
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            return raw[:512].decode("utf-8", errors="replace")

        def _scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {
                    k: ("***" if k.lower() in _REDACT_KEYS else _scrub(v))
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [_scrub(v) for v in obj]
            return obj

        scrubbed = _scrub(payload)
        return json.dumps(scrubbed, default=str)[:512]
    except Exception as exc:  # pragma: no cover - logging fallback only
        return f"<failed to render body: {exc}>"


def _coerce_errors(detail: Any) -> Any:
    """Normalise DRF error detail to a JSON-friendly shape."""
    if isinstance(detail, list):
        return [_coerce_errors(d) for d in detail]
    if isinstance(detail, dict):
        return {k: _coerce_errors(v) for k, v in detail.items()}
    if isinstance(detail, bytes):
        return detail.decode("utf-8", errors="replace")
    return str(detail)


def smsly_exception_handler(exc, context) -> Response | None:
    """Wrap DRF's default handler so we:
      * always log the body + serializer errors for 4xx responses
      * return a consistent JSON envelope
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        # Non-DRF exception (e.g. Django middleware raised). Let it bubble.
        return None

    request = context.get("request")
    view = context.get("view")
    detail = response.data.get("detail") if isinstance(response.data, dict) else None

    if isinstance(exc, ValidationError):
        errors = _coerce_errors(response.data)
        code = "validation_error"
    elif isinstance(exc, Throttled):
        # Surface the retry-after value as a structured field so clients
        # don't have to regex-parse the human-readable detail string.
        # ``wait`` is a float seconds value set by DRF's Throttled exception;
        # the Retry-After header is also set here defensively (DRF's default
        # handler does the same, but custom exception handlers can drop it
        # if they rebuild response.data carelessly).
        wait = getattr(exc, "wait", None)
        errors = _coerce_errors(detail or "Too Many Requests")
        code = "throttled"
        if wait is not None:
            try:
                response["Retry-After"] = str(int(wait))
            except (TypeError, ValueError):
                pass
        response.data = {
            "error": errors,
            "code": code,
            "status": response.status_code,
        }
        if wait is not None:
            try:
                response.data["wait_seconds"] = int(wait)
            except (TypeError, ValueError):
                pass
        return response
    elif isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
        errors = _coerce_errors(detail or "Authentication required.")
        code = "unauthenticated"
    elif isinstance(exc, PermissionDenied):
        errors = _coerce_errors(detail or "Permission denied.")
        code = "permission_denied"
    elif isinstance(exc, APIException):
        errors = _coerce_errors(detail or exc.default_detail)
        code = getattr(exc, "default_code", "api_error")
    else:  # pragma: no cover - DRF only reaches here for APIException subclasses
        errors = _coerce_errors(detail or "Request failed.")
        code = "api_error"

    body_log = _safe_body_for_log(request) if request is not None else "<no request>"

    if 400 <= response.status_code < 500:
        logger.warning(
            "api_4xx path=%s method=%s status=%s view=%s code=%s errors=%s body=%s",
            getattr(request, "path", "?"),
            getattr(request, "method", "?"),
            response.status_code,
            view.__class__.__name__ if view else "?",
            code,
            errors,
            body_log,
        )
    else:
        logger.error(
            "api_5xx path=%s method=%s status=%s view=%s code=%s errors=%s body=%s",
            getattr(request, "path", "?"),
            getattr(request, "method", "?"),
            response.status_code,
            view.__class__.__name__ if view else "?",
            code,
            errors,
            body_log,
        )

    response.data = {
        "error": errors,
        "code": code,
        "status": response.status_code,
    }
    return response
