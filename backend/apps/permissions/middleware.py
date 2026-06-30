"""Middleware that audits permission denials for authenticated users.

Every 403 response returned to an authenticated user is recorded in the
``PermissionDeniedAudit`` table so security teams can detect probing,
misconfigured policies, and insider threats.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class PermissionAuditMiddleware:
    """Capture every 403 for authenticated users and persist an audit record.

    The middleware inspects the response *after* the view has run. If the
    status is 403 and the user is authenticated, it creates a
    ``PermissionDeniedAudit`` entry.

    The optional ``request._denied_permission`` attribute (set by
    ``HasPermission.has_permission()``) provides the specific permission
    code that was denied.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)

        if response.status_code != 403:
            return response

        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        try:
            from apps.permissions.models import PermissionDeniedAudit

            PermissionDeniedAudit.objects.create(
                user=request.user,
                path=request.path[:500],
                method=request.method[:10],
                permission_code=getattr(request, '_denied_permission', 'unknown'),
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
        except Exception:
            logger.exception("Failed to write permission denial audit record")

        return response
