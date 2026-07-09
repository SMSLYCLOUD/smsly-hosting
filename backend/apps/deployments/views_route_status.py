"""DNS/SSL route status API view.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``RouteStatusView`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from rest_framework import authentication, permissions
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from .models import (
    PlatformConfig,  # type: ignore[attr-defined]    # re-exported via models.py; mypy can't see through the empty hub module.
)


class RouteStatusView(GenericAPIView):
    """
    Authenticated DNS/SSL status check for the platform domain.
    """

    authentication_classes = [authentication.SessionAuthentication, authentication.TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):  # pylint: disable=unused-argument
        import socket
        import ssl
        from datetime import datetime

        cfg = PlatformConfig.load()
        domains = [d for d in [cfg.domain] if d]
        entries = []

        def _resolve(host):
            try:
                return socket.gethostbyname(host)
            except Exception:
                return None

        def _cert_expiry(host):
            try:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
                    s.settimeout(4.0)
                    s.connect((host, 443))
                    cert = s.getpeercert()
                not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                return not_after.isoformat()
            except Exception:
                return None

        for host in domains:
            resolved = _resolve(host)
            entries.append(
                {
                    "host": host,
                    "resolved_ip": resolved,
                    "matches_server_ip": bool(resolved and cfg.server_ip and resolved == cfg.server_ip),
                    "cert_not_after": _cert_expiry(host) if cfg.use_ssl else None,
                }
            )

        return Response(
            {
                "domain": cfg.domain,
                "use_ssl": cfg.use_ssl,
                "wildcard_subdomains": cfg.wildcard_subdomains,
                "server_ip": cfg.server_ip,
                "entries": entries,
            }
        )
