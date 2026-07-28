"""
Provisioning mixins for ManagedServerViewSet.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response

from ...models.servers import ManagedServer
from .serializers import (
    ManagedServerProvisionSerializer,
    ManagedServerSerializer,
    ServerProvisionThrottle,
)


class ProvisioningMixin:

    @action(detail=False, methods=["post"], url_path="provision")
    @throttle_classes([ServerProvisionThrottle])
    def provision_new(self, request):
        is_primary_raw = request.data.get("is_primary", False)
        if isinstance(is_primary_raw, str):
            is_primary_requested = is_primary_raw.strip().lower() in (
                "true", "1", "yes", "t", "on",
            )
        else:
            is_primary_requested = bool(is_primary_raw)
        if is_primary_requested and not request.user.is_superuser:
            return Response(
                {"error": "Only superusers can provision a primary server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data.copy()
        validated.pop("ssh_auth_method", None)
        validated.pop("node_certificate", None)

        server = ManagedServer.objects.create(
            owner=request.user,
            provision_status=ManagedServer.ProvisionStatus.PENDING,
            **validated,
        )
        if server.is_primary and server.allow_user_workloads:
            server.allow_user_workloads = False
            server.save(update_fields=["allow_user_workloads", "updated_at"])

        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="provision-batch")
    @throttle_classes([ServerProvisionThrottle])
    def provision_batch(self, request):
        servers_data = request.data.get("servers")
        if not servers_data or not isinstance(servers_data, list):
            return Response(
                {"error": "'servers' must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(servers_data) > 20:
            return Response(
                {"error": "Maximum 20 servers per batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        from .services.provisioner import provision_server

        for idx, item in enumerate(servers_data):
            serializer = ManagedServerProvisionSerializer(data=item)
            if not serializer.is_valid():
                errors.append({"index": idx, "host": item.get("host", ""), "errors": serializer.errors})
                continue

            validated = serializer.validated_data.copy()
            validated.pop("ssh_auth_method", None)
            validated.pop("node_certificate", None)

            validated["is_lite_agent"] = True
            validated["is_primary"] = False

            server = ManagedServer.objects.create(
                owner=request.user,
                provision_status=ManagedServer.ProvisionStatus.PENDING,
                **validated,
            )
            provision_server.delay(str(server.id))
            created.append(ManagedServerSerializer(server).data)

        return Response(
            {"created": created, "errors": errors, "total": len(created)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    @action(detail=True, methods=["post"], url_path="retry-provision")
    @throttle_classes([ServerProvisionThrottle])
    def retry_provision(self, request, pk=None):
        server = self.get_object()

        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Retry started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="update-server")
    @throttle_classes([ServerProvisionThrottle])
    def update_server(self, request, pk=None):
        server = self.get_object()

        blocked_statuses = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        if server.provision_status in blocked_statuses:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "update_server: auto-clearing in-flight provision_status=%s for server %s (user=%s)",
                server.provision_status, server.id, request.user.id,
            )
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            server.save(update_fields=["provision_status", "updated_at"])

        if not (server.ssh_key or server.ssh_password):
             return Response(
                 {"error": "Server has no SSH credentials configured for updates."},
                 status=status.HTTP_400_BAD_REQUEST,
             )

        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Update started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        from .services.provisioner import provision_server
        provision_server.delay(str(server.id), skip_reboot=True)

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )
