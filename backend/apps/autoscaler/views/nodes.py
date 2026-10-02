"""Per-node autoscaler API — scores, capacity, replicas, drain/cordon."""
import logging

from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models.core import ManagedServer
from apps.autoscaler.models.replica import ServiceReplica
from apps.deployments.services.node_scorer import NodeScorer

logger = logging.getLogger(__name__)


class AutoscalerNodesViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"error": "Admin privileges required."}, status=403)
        from django.core.cache import cache
        cached = cache.get("smsly:autoscaler:nodes:v1")
        if cached is not None:
            return Response(cached)
        servers = list(ManagedServer.objects.filter(is_primary=False)[:50])
        scorer = NodeScorer()
        try:
            scored = {s.id: (score, res) for s, score, res in scorer.score(servers)}
        except Exception:
            scored = {}
        rows = []
        for server in servers:
            score, resources = scored.get(server.id, (-1, {}))
            replica_qs = ServiceReplica.objects.filter(node=server).exclude(status="DESTROYED")
            storage = None
            try:
                from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
                storage = RemoteOrchestrator(server).get_node_storage_overview()
            except Exception as exc:
                logger.debug("Node storage fetch failed for %s: %s", server.name, exc)
            rows.append({
                "id": str(server.id),
                "name": server.name,
                "host": server.host,
                "status": server.status,
                "node_type": getattr(server, "node_type", ""),
                "is_lite_agent": bool(getattr(server, "is_lite_agent", False)),
                "score": score,
                "resources": resources or {},
                "replica_count": replica_qs.count(),
                "replicas": [
                    {"id": str(r.id), "service": str(r.service_id), "status": r.status}
                    for r in replica_qs[:50]
                ],
                "storage": (storage or {}).get("disk") if storage else None,
                "last_heartbeat": getattr(server, "last_agent_heartbeat_at", None),
            })
        payload = {"nodes": rows}
        try:
            cache.set("smsly:autoscaler:nodes:v1", payload, 30)
        except Exception:
            pass
        return Response(payload)

    @action(detail=True, methods=["post"])
    def drain(self, request, pk=None):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"error": "Admin privileges required."}, status=403)
        try:
            server = ManagedServer.objects.get(id=pk)
        except Exception:
            return Response({"error": "Node not found."}, status=404)
        moved = 0
        for replica in ServiceReplica.objects.filter(node=server).exclude(status="DESTROYED"):
            try:
                from apps.deployments.services.spawning_service import SpawningService
                SpawningService().destroy(replica)
                moved += 1
            except Exception as exc:
                logger.warning("Failed to drain replica %s: %s", replica.id, exc)
        server.allow_user_workloads = False
        server.save(update_fields=["allow_user_workloads", "updated_at"])
        return Response({"ok": True, "drained_replicas": moved})

    @action(detail=True, methods=["post"])
    def cordon(self, request, pk=None):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"error": "Admin privileges required."}, status=403)
        try:
            server = ManagedServer.objects.get(id=pk)
        except Exception:
            return Response({"error": "Node not found."}, status=404)
        payload = request.data if isinstance(request.data, dict) else {}
        allow = payload.get("allow_user_workloads", True)
        server.allow_user_workloads = bool(allow)
        server.save(update_fields=["allow_user_workloads", "updated_at"])
        return Response({"ok": True, "allow_user_workloads": server.allow_user_workloads})
