"""platform views."""
import logging
import os

logger = logging.getLogger(__name__)



import shutil
import socket
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from ..models import Deployment
from ._helpers import EmptySerializer
class PlatformResourcesView(GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):

        import psutil

        from apps.deployments.models import ManagedServer, Service
        is_admin = bool(
            request.user and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )
        vm = psutil.virtual_memory()
        disk = shutil.disk_usage("/")
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        services = Service.objects.only("id", "name", "owner__id", "server__id")
        if not is_admin:
            services = services.filter(owner=request.user)
        running = services.filter(deployments__status=Deployment.Status.ACTIVE).distinct().count()
        failed = services.filter(deployments__status=Deployment.Status.FAILED).distinct().count()
        building = services.filter(
            deployments__status__in=[Deployment.Status.BUILDING, Deployment.Status.DEPLOYING]
        ).distinct().count()
        servers = ManagedServer.objects.only(
            "id", "name", "host", "owner__id", "is_primary", "status", "agent_runtime_info"
        )
        if not is_admin:
            servers = servers.filter(owner=request.user)

        nodes = []
        for s in servers:
            svc_scope = services.filter(server=s) if hasattr(Service, "server") else services
            svc_running = svc_scope.filter(deployments__status=Deployment.Status.ACTIVE).distinct().count()
            svc_failed = svc_scope.filter(deployments__status=Deployment.Status.FAILED).distinct().count()
            svc_building = svc_scope.filter(
                deployments__status__in=[Deployment.Status.BUILDING, Deployment.Status.DEPLOYING]
            ).distinct().count()
            if s.is_primary:
                nodes.append({
                    "id": str(s.id),
                    "name": s.name,
                    "provider": "managed",
                    "region": "primary",
                    "status": "healthy" if vm.percent < 80 else "warning",
                    "public_ip": s.host,
                    "cpu": {"cores": psutil.cpu_count() or 0, "load_average": [round(load[0], 2), round(load[1], 2), round(load[2], 2)]},
                    "memory": {"total_mb": round(vm.total / (1024 ** 2), 2), "used_mb": round(vm.used / (1024 ** 2), 2), "free_mb": round(vm.available / (1024 ** 2), 2), "usage_percent": round(vm.percent, 2)},
                    "disk": {"total_gb": round(disk.total / (1024 ** 3), 2), "used_gb": round(disk.used / (1024 ** 3), 2), "free_gb": round(disk.free / (1024 ** 3), 2), "usage_percent": round((disk.used / max(1, disk.total)) * 100, 2)},
                    "containers": {"running": svc_running, "failed": svc_failed, "building": svc_building},
                    "uptime_seconds": int(timezone.now().timestamp() - psutil.boot_time()),
                    "warnings": ["High memory pressure"] if vm.percent >= 85 else [],
                })
            else:
                rt = s.agent_runtime_info if isinstance(s.agent_runtime_info, dict) else {}
                mem_total_mb = float(rt.get("memory_total_mb") or 0.0)
                mem_used_mb = float(rt.get("memory_used_mb") or 0.0)
                mem_pct = float(rt.get("memory_percent") or (round((mem_used_mb / mem_total_mb) * 100, 2) if mem_total_mb > 0 else 0.0))
                disk_total_gb = float(rt.get("disk_total_gb") or 0.0)
                disk_used_gb = float(rt.get("disk_used_gb") or 0.0)
                disk_pct = float(rt.get("disk_percent") or (round((disk_used_gb / disk_total_gb) * 100, 2) if disk_total_gb > 0 else 0.0))
                node_status = (
                    "healthy" if s.status == ManagedServer.Status.ONLINE
                    else "warning" if s.status == ManagedServer.Status.DEGRADED
                    else "offline"
                )
                nodes.append({
                    "id": str(s.id),
                    "name": s.name,
                    "provider": "managed",
                    "region": "remote",
                    "status": node_status,
                    "public_ip": s.host,
                    "cpu": {"cores": int(rt.get("cpu_cores") or 0), "load_average": rt.get("load_average") or [0.0, 0.0, 0.0]},
                    "memory": {"total_mb": mem_total_mb, "used_mb": mem_used_mb, "free_mb": max(0.0, round(mem_total_mb - mem_used_mb, 2)), "usage_percent": mem_pct},
                    "disk": {"total_gb": disk_total_gb, "used_gb": disk_used_gb, "free_gb": max(0.0, round(disk_total_gb - disk_used_gb, 2)), "usage_percent": disk_pct},
                    "containers": {"running": svc_running, "failed": svc_failed, "building": svc_building},
                    "uptime_seconds": int(rt.get("uptime_seconds") or 0),
                    "warnings": ["Node offline"] if node_status == "offline" else (["High memory pressure"] if mem_pct >= 85 else []),
                })

        if not nodes and is_admin:
            nodes = [{
                "id": "local-node",
                "name": socket.gethostname(),
                "provider": "local",
                "region": "unknown",
                "status": "healthy" if vm.percent < 80 else "warning",
                "cpu": {"cores": psutil.cpu_count() or 0, "load_average": [round(load[0], 2), round(load[1], 2), round(load[2], 2)]},
                "memory": {"total_mb": round(vm.total / (1024 ** 2), 2), "used_mb": round(vm.used / (1024 ** 2), 2), "free_mb": round(vm.available / (1024 ** 2), 2), "usage_percent": round(vm.percent, 2)},
                "disk": {"total_gb": round(disk.total / (1024 ** 3), 2), "used_gb": round(disk.used / (1024 ** 3), 2), "free_gb": round(disk.free / (1024 ** 3), 2), "usage_percent": round((disk.used / max(1, disk.total)) * 100, 2)},
                "containers": {"running": running, "failed": failed, "building": building},
                "uptime_seconds": int(timezone.now().timestamp() - psutil.boot_time()),
                "warnings": [],
            }]
        return Response({"nodes": nodes, "summary": {"total_nodes": len(nodes), "healthy_nodes": sum(1 for n in nodes if n["status"] == "healthy"), "critical_nodes": sum(1 for n in nodes if n["status"] == "offline"), "total_ram_mb": sum(n["memory"]["total_mb"] for n in nodes), "used_ram_mb": sum(n["memory"]["used_mb"] for n in nodes), "total_disk_gb": sum(n["disk"]["total_gb"] for n in nodes), "used_disk_gb": sum(n["disk"]["used_gb"] for n in nodes)}})


class PlatformConfigViewSet(viewsets.GenericViewSet):
    """
    ViewSet for platform-wide configurations and Infisical secret synchronization.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        from apps.deployments.models.core import PlatformConfig
        return PlatformConfig.objects.all()

    @action(detail=False, methods=["post"], url_path="sync-infisical")
    def sync_infisical(self, request):
        from apps.deployments.services.infisical import (
            get_infisical_client,
            get_or_create_workspace,
            pull_platform_config_from_infisical,
            push_platform_config_to_infisical,
        )
        client = get_infisical_client()
        if not client:
            return Response(
                {"error": "Infisical client not configured or unreachable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        ws_id = get_or_create_workspace(client)
        if not ws_id:
            return Response(
                {"error": "Failed to resolve Infisical workspace."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        pull_res = pull_platform_config_from_infisical(client, ws_id)
        push_res = push_platform_config_to_infisical(client, ws_id)

        pushed = push_res.get("synced", [])
        pulled = pull_res.get("synced", [])
        failed = push_res.get("failed", []) + pull_res.get("failed", [])

        return Response({
            "status": "success" if not failed else "partial",
            "synced_count": len(pushed) + len(pulled),
            "pushed": pushed,
            "pulled": pulled,
            "failed": failed,
        })

