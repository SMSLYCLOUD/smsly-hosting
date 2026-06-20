"""Auto-scaling API: analyze services and manage replicas."""
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models_core import ManagedServer, Service
from apps.deployments.models_replica import ServiceReplica
from apps.deployments.services.node_scorer import NodeScorer
from apps.deployments.services.spawning_service import SpawningService


class ServiceReplicaSerializer(serializers.ModelSerializer):
    node_name = serializers.CharField(source='node.name', read_only=True)
    node_host = serializers.CharField(source='node.host', read_only=True)

    class Meta:
        model = ServiceReplica
        fields = ['id', 'service', 'node', 'node_name', 'node_host',
                  'container_name', 'status', 'metrics_snapshot',
                  'spawn_reason', 'created_at', 'destroyed_at']
        read_only_fields = ['id', 'container_name', 'status',
                           'metrics_snapshot', 'created_at', 'destroyed_at']


class AlertConfigSerializer(serializers.Serializer):
    """Autoscaler alert thresholds + notification preferences per service."""
    cpu_warning = serializers.IntegerField(min_value=0, max_value=100, required=False)
    cpu_critical = serializers.IntegerField(min_value=0, max_value=100, required=False)
    memory_warning = serializers.IntegerField(min_value=0, max_value=100, required=False)
    memory_critical = serializers.IntegerField(min_value=0, max_value=100, required=False)
    disk_warning = serializers.IntegerField(min_value=0, max_value=100, required=False)
    disk_critical = serializers.IntegerField(min_value=0, max_value=100, required=False)
    notify_email = serializers.BooleanField(required=False)
    notify_webhook = serializers.BooleanField(required=False)
    webhook_url = serializers.URLField(required=False, allow_blank=True)


class ScalingViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['post'])
    def analyze(self, request, pk=None):
        """Analyze a service and return scaling recommendation.

        Uses the unified DecisionEngine (same as the Celery periodic
        autoscale task) so the REST endpoint and the automatic scaling
        always agree on the recommendation.
        """
        from apps.autoscaler.engine.pipeline import analyze_only
        service = Service.objects.get(id=pk, owner=request.user)
        result = analyze_only(service)
        return Response(result)

    @action(detail=True, methods=['get', 'put'])
    def alert_config(self, request, pk=None):
        """Get or update per-service autoscaler alert thresholds."""
        service = get_object_or_404(Service, id=pk, owner=request.user)
        if request.method == 'GET':
            return Response(service.alert_config or {})
        serializer = AlertConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        merged = dict(service.alert_config or {})
        merged.update(serializer.validated_data)
        service.alert_config = merged
        service.save(update_fields=['alert_config', 'updated_at'])
        return Response(merged)

    @action(detail=True, methods=['post'])
    def spawn(self, request, pk=None):
        """Manually spawn a replica on the best available node."""
        service = Service.objects.get(id=pk, owner=request.user)

        candidates = ManagedServer.objects.filter(
            status=ManagedServer.Status.ONLINE,
            allow_user_workloads=True,
        ).exclude(is_primary=True)

        if not candidates.exists():
            return Response({'error': 'No available nodes'}, status=400)

        scorer = NodeScorer()
        best = scorer.best(candidates)
        if not best:
            return Response({'error': 'All nodes too loaded'}, status=400)

        replica = ServiceReplica.objects.create(
            service=service, node=best, status='SPAWNING',
            spawn_reason='Manual spawn via API',
        )

        spawner = SpawningService()
        try:
            spawner.spawn(service, best, replica)
            return Response(ServiceReplicaSerializer(replica).data)
        except Exception as exc:
            replica.status = 'DESTROYED'
            replica.save(update_fields=['status'])
            return Response({'error': str(exc)}, status=500)
        finally:
            spawner.cleanup()

    @action(detail=False, methods=['get'])
    def replicas(self, request):
        """List replicas for a service. Pass ?service=<uuid>."""
        service_id = request.GET.get('service')
        if not service_id:
            return Response({'error': '?service=UUID required'}, status=400)
        replicas = ServiceReplica.objects.filter(
            service__id=service_id,
            service__owner=request.user,
        ).order_by('-created_at')
        return Response(ServiceReplicaSerializer(replicas, many=True).data)

    @action(detail=False, methods=['delete'])
    def destroy_replica(self, request):
        """Destroy a specific replica. Pass ?id=<replica_uuid>."""
        replica_id = request.GET.get('id')
        if not replica_id:
            return Response({'error': '?id=UUID required'}, status=400)
        replica = ServiceReplica.objects.get(
            id=replica_id, service__owner=request.user, status='RUNNING',
        )
        spawner = SpawningService()
        try:
            spawner.destroy(replica)
            return Response({'status': 'destroyed'})
        finally:
            spawner.cleanup()
