"""
Agent endpoint mixins for ManagedServerViewSet.
"""

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models.servers import ManagedServer
from .helpers import _append_log_safe, _truncate_dict


class AgentMixin:

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-ready",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_ready(self, request, pk=None):
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self._get_object_for_agent(pk)
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        update_fields = ["agent_ready", "last_agent_heartbeat_at", "updated_at"]
        server.agent_ready = True
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
            update_fields.append("agent_runtime_info")
        if server.provision_status in {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
        }:
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            update_fields.append("provision_status")
        server.save(update_fields=update_fields)

        _append_log_safe(
            server,
            f"✅ Agent ready: runtime={_truncate_dict(runtime_info)}",
        )

        return Response({
            "agent_ready": True,
            "server_id": str(server.id),
            "node_id": runtime_info.get("node_id", ""),
            "master_time": timezone.now().isoformat(),
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-heartbeat",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_heartbeat(self, request, pk=None):
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self._get_object_for_agent(pk)
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        status_payload = ""
        if isinstance(request.data, dict):
            status_payload = str(request.data.get("status", "") or "").strip()

        update_fields = [
            "last_agent_heartbeat_at",
            "agent_runtime_info",
            "agent_ready",
            "updated_at",
        ]
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
        if not server.agent_ready:
            server.agent_ready = True
            _append_log_safe(
                server,
                "✅ Agent ready (implicit via first heartbeat)",
            )
        server.save(update_fields=update_fields)

        if status_payload.lower() in {"degraded", "down", "unhealthy"}:
            if server.status == ManagedServer.Status.ONLINE:
                server.status = ManagedServer.Status.DEGRADED
                server.save(update_fields=["status", "updated_at"])

        return Response({
            "server_id": str(server.id),
            "master_time": timezone.now().isoformat(),
        })
