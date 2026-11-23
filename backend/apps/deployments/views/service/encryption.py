"""Per-service opt-in encryption at rest & DB/Redis point-of-contact lock actions."""
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crypto_daemon import (
    CryptdError,
    cryptd_client,
    register_and_unlock_service,
)
from apps.teams.permissions import assert_can_write
from .._helpers import _parse_bool

logger = logging.getLogger(__name__)


class EncryptionActionsMixin:
    """User opt-in / opt-out encryption controls on ServiceViewSet."""

    @action(detail=True, methods=["get", "post"], url_path="encryption")
    def encryption_config(self, request, pk=None):
        """
        GET  /api/v1/services/{id}/encryption/ -> inspect opt-in state & cryptd policy
        POST /api/v1/services/{id}/encryption/ -> enable/disable per-service encryption at rest
             Body: { "enabled": true|false }
        """
        service = self.get_object()
        daemon_enabled = cryptd_client.is_enabled()

        if request.method.upper() == "GET":
            policy = None
            daemon_status = None
            if daemon_enabled:
                try:
                    policy = cryptd_client.get_service_policy(str(service.id))
                    daemon_status = cryptd_client.get_status()
                except CryptdError as exc:
                    policy = {"error": str(exc)}
            return Response(
                {
                    "service_id": str(service.id),
                    "encryption_at_rest_enabled": bool(service.encryption_at_rest_enabled),
                    "platform_cryptd_enabled": daemon_enabled,
                    "policy": policy,
                    "daemon_status": daemon_status,
                }
            )

        assert_can_write(self.request.user, service)
        if "enabled" not in request.data and "encryption_at_rest_enabled" not in request.data:
            return Response(
                {"error": "Provide 'enabled' (boolean) to opt in or opt out."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw = request.data.get("enabled", request.data.get("encryption_at_rest_enabled"))
        enabled = _parse_bool(raw)
        service.encryption_at_rest_enabled = enabled
        service.save(update_fields=["encryption_at_rest_enabled", "updated_at"])

        sync_result = None
        if daemon_enabled:
            try:
                sync_result = register_and_unlock_service(service, unlock_now=enabled)
            except CryptdError as exc:
                logger.warning(
                    "Failed syncing encryption opt-in=%s for service %s: %s",
                    enabled,
                    service.id,
                    exc,
                )
                sync_result = {"ok": False, "error": str(exc)}

        return Response(
            {
                "service_id": str(service.id),
                "encryption_at_rest_enabled": enabled,
                "platform_cryptd_enabled": daemon_enabled,
                "sync": sync_result,
            }
        )

    @action(detail=True, methods=["post"], url_path="encryption/lock")
    def encryption_lock(self, request, pk=None):
        """Immediately sever Postgres/Redis connections and lock keys for this service."""
        service = self.get_object()
        assert_can_write(self.request.user, service)
        if not service.encryption_at_rest_enabled:
            return Response(
                {"error": "Service has not opted in to encryption at rest."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not cryptd_client.is_enabled():
            return Response(
                {"error": "grid-cryptd daemon is not enabled on this platform."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        reason = str(request.data.get("reason") or f"locked by {request.user.get_username()}")
        try:
            res = cryptd_client.lock_service(str(service.id), reason=reason)
        except CryptdError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(res)

    @action(detail=True, methods=["post"], url_path="encryption/unlock")
    def encryption_unlock(self, request, pk=None):
        """Unlock/renew database, Redis, and volume key lease for this service."""
        service = self.get_object()
        assert_can_write(self.request.user, service)
        if not service.encryption_at_rest_enabled:
            return Response(
                {"error": "Service has not opted in to encryption at rest."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not cryptd_client.is_enabled():
            return Response(
                {"error": "grid-cryptd daemon is not enabled on this platform."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        raw_ttl = request.data.get("ttl_seconds")
        try:
            ttl = 7200 if raw_ttl in (None, "") else int(raw_ttl)
        except (TypeError, ValueError):
            return Response(
                {"error": "ttl_seconds must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if ttl <= 0:
            return Response(
                {"error": "ttl_seconds must be greater than zero."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            res = cryptd_client.unlock_service(
                str(service.id),
                project_id=str(service.project_id or "default"),
                ttl_seconds=ttl,
            )
        except CryptdError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_423_LOCKED)
        return Response(res)
