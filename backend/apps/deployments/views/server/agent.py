"""
Agent endpoint mixins for ManagedServerViewSet.
"""

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models.servers import ManagedServer
from .helpers import _append_log_safe, _truncate_dict

logger = logging.getLogger(__name__)


def _persist_media_runtime(server, runtime_info):
    if str(getattr(server, "node_type", "")) != ManagedServer.NodeType.MEDIA:
        return
    try:
        from apps.media.models import MediaNodeProfile

        profile = MediaNodeProfile.objects.filter(server=server).first()
        if not profile:
            return
        capacity = runtime_info.get("capacity") or {}
        profile.service_status = runtime_info.get("services") or {}
        profile.active_calls = int(capacity.get("active_calls") or 0)
        profile.active_rooms = int(capacity.get("active_rooms") or 0)
        profile.active_participants = int(capacity.get("active_participants") or 0)
        profile.capacity_score = float(capacity.get("score") or 0)
        profile.last_telemetry_at = timezone.now()
        profile.save(update_fields=[
            "service_status", "active_calls", "active_rooms",
            "active_participants", "capacity_score",
            "last_telemetry_at", "updated_at",
        ])
    except Exception:
        logger.exception("Failed to persist media heartbeat for %s", server.id)


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

        _persist_media_runtime(server, runtime_info)

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
        _persist_media_runtime(server, runtime_info)

        if status_payload.lower() in {"degraded", "down", "unhealthy"}:
            if server.status == ManagedServer.Status.ONLINE:
                server.status = ManagedServer.Status.DEGRADED
                server.save(update_fields=["status", "updated_at"])
        else:
            if server.status != ManagedServer.Status.ONLINE:
                server.status = ManagedServer.Status.ONLINE
                server.save(update_fields=["status", "updated_at"])

        return Response({
            "server_id": str(server.id),
            "master_time": timezone.now().isoformat(),
        })

    @action(
        detail=True,
        methods=["get"],
        url_path="mesh-dns-zone",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def mesh_dns_zone(self, request, pk=None):
        """Serve the mesh DNS zone to a node (HMAC auth via gateway_secret).

        Explicit URL (not router action) like agent-ready/agent-heartbeat:
        GET /api/v1/servers/<uuid>/mesh-dns-zone/ (see deployments.urls).
        The node's sync script polls this every 5 min and atomically
        rewrites its local CoreDNS zone files; CoreDNS auto-reloads.
        Read-only: never mutates the server row.
        """
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )
        from apps.deployments.services.mesh_dns import (
            build_corefile,
            build_mesh_hosts,
            mesh_dns_domain,
        )

        server = self._get_object_for_agent(pk)
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            domain = mesh_dns_domain()
            hosts_content, record_count = build_mesh_hosts()
            corefile_content = build_corefile()
        except Exception as exc:
            logger.warning("mesh-dns-zone render failed: %s", exc)
            return Response(
                {"error": "Zone render failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({
            "server_id": str(server.id),
            "domain": domain,
            "records": record_count,
            "hosts": hosts_content,
            "corefile": corefile_content,
            "master_time": timezone.now().isoformat(),
        })
