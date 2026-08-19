"""Auto-scaling API: analyze services and manage replicas."""
import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models.core import ManagedServer, Service
from apps.autoscaler.models.replica import ServiceReplica
from apps.deployments.services.node_scorer import NodeScorer, _get_min_score
from apps.deployments.services.spawning_service import SpawningService
from apps.teams.permissions import get_team_q_filter

logger = logging.getLogger(__name__)


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
    # Per-service cooldown overrides (minutes). When set, they take
    # precedence over the global SCALE_COOLDOWN_* env vars for this service.
    cooldown_up_min = serializers.IntegerField(min_value=0, required=False)
    cooldown_down_min = serializers.IntegerField(min_value=0, required=False)


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
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(
                Service, get_team_q_filter(request.user, request=request), id=pk
            )
        result = analyze_only(service)
        return Response(result)

    @action(detail=True, methods=['get', 'put'])
    def alert_config(self, request, pk=None):
        """Get or update per-service autoscaler alert thresholds."""
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(Service, get_team_q_filter(request.user, request=request), id=pk)
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
        """Manually spawn a replica.

        Query params:
            mode=horizontal  — local only (same server, no SSH needed)
            mode=vertical    — remote only (different server, requires SSH)
            (default)        — local first, then remote fallback
        """
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(
                Service, get_team_q_filter(request.user, request=request), id=pk
            )

        mode = request.query_params.get('mode', '').lower() or request.data.get('mode', '').lower() or ''

        # Check max_replicas cap
        running = ServiceReplica.objects.filter(
            service=service, status='RUNNING'
        ).count()
        if running >= (service.max_replicas or 1):
            return Response({
                'error': f'At max_replicas ({service.max_replicas}) — cannot spawn more.',
            }, status=400)

        spawner = SpawningService()

        # --- Horizontal: local only ---
        if mode != 'vertical':
            replica = ServiceReplica.objects.create(
                service=service, node=None, status='SPAWNING',
                spawn_reason='Manual spawn via API (horizontal)',
            )
            try:
                spawner.spawn_local(service, replica)
                return Response(ServiceReplicaSerializer(replica).data)
            except Exception as exc:
                logger.warning("Local spawn failed for %s: %s", service.name, exc)
                replica.status = 'DESTROYED'
                replica.save(update_fields=['status'])
                if mode == 'horizontal':
                    return Response({
                        'error': f'Local spawn failed: {exc}',
                        'hint': 'Ensure Docker is running and the service has a docker_image set.',
                    }, status=500)
                # else: fall through to remote

        # --- Vertical: remote nodes ---
        allow_control_plane = getattr(
            settings, 'GRID_ALLOW_CONTROL_PLANE_WORKLOADS',
            getattr(settings, 'CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS', False),
        )
        candidates = ManagedServer.objects.filter(
            status=ManagedServer.Status.ONLINE,
            allow_user_workloads=True,
        )
        if not allow_control_plane:
            candidates = candidates.exclude(is_primary=True)

        if not candidates.exists():
            return Response({
                'error': 'No available remote nodes.',
                'hint': 'Horizontal scaling runs on the same server. Remote nodes require SSH keys configured on ManagedServer records.',
            }, status=400)

        scorer = NodeScorer()
        best = scorer.best(candidates)
        if not best:
            min_score = _get_min_score()
            ranked = scorer.score(candidates)
            return Response({
                'error': 'All remote nodes too loaded',
                'min_score': min_score,
                'node_scores': [
                    {'node': n.name, 'score': round(s, 1), **r}
                    for n, s, r in ranked
                ],
            }, status=400)

        replica = ServiceReplica.objects.create(
            service=service, node=best, status='SPAWNING',
            spawn_reason='Manual spawn via API (vertical)',
        )
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
        if request.user.is_superuser:
            replicas = ServiceReplica.objects.filter(
                service__id=service_id,
            ).order_by('-created_at')
        else:
            replicas = ServiceReplica.objects.filter(
                get_team_q_filter(request.user, prefix='service__', request=request),
                service__id=service_id,
            ).order_by('-created_at')
        return Response(ServiceReplicaSerializer(replicas, many=True).data)

    @action(detail=False, methods=['delete'])
    def destroy_replica(self, request):
        """Destroy a specific replica. Pass ?id=<replica_uuid>."""
        replica_id = request.GET.get('id')
        if not replica_id:
            return Response({'error': '?id=UUID required'}, status=400)
        if request.user.is_superuser:
            replica = get_object_or_404(
                ServiceReplica, id=replica_id, status='RUNNING',
            )
        else:
            replica = get_object_or_404(
                ServiceReplica,
                get_team_q_filter(request.user, prefix='service__', request=request),
                id=replica_id, status='RUNNING',
            )
        spawner = SpawningService()
        # Refuse to destroy the last replica when min_replicas >= 1, so a
        # service cannot be taken to zero by manual destroy. To take a
        # service to zero, set min_replicas=0 first.
        running = ServiceReplica.objects.filter(
            service=replica.service, status='RUNNING'
        ).count()
        if running <= (replica.service.min_replicas or 0):
            return Response({
                'error': f'Cannot destroy replica — service is at min_replicas '
                f'({replica.service.min_replicas or 0}). Set min_replicas=0 first.',
            }, status=400)
        try:
            spawner.destroy(replica)
            return Response({'status': 'destroyed'})
        finally:
            spawner.cleanup()

    @action(detail=True, methods=['post'])
    def apply_vpa(self, request, pk=None):
        """Apply VPA resource limits to a service's running container(s).

        Works for both local containers and remote nodes (via SSH).
        Auth method depends on the ManagedServer's stored credentials.
        """
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(
                Service, get_team_q_filter(request.user, request=request), id=pk
            )

        from apps.autoscaler.services.tasks_autoscale import apply_vpa_limits_task

        # Apply VPA limits for this specific service
        import docker as docker_lib
        from apps.deployments.services.ssh_client import SSHClient

        ceiling = 1.5
        memory = service.memory_mb
        cpu = int(service.cpu_cores * 1024) if service.cpu_cores else 0

        update_parts = []
        if memory and memory > 0:
            update_parts.append(f"--memory={memory}m")
            update_parts.append(f"--memory-reservation={memory}m")
        if cpu and cpu > 0:
            update_parts.append(f"--cpu-shares={max(2, int((cpu / 1000) * 1024))}")
            update_parts.append("--cpu-period=100000")
            update_parts.append(f"--cpu-quota={int((cpu / 1000) * 100000 * ceiling)}")
        if not update_parts:
            return Response({'error': 'No memory/cpu settings to apply'}, status=400)

        update_cmd = " ".join(update_parts)
        container_name = service.name

        if service.server_id:
            node = service.server
            if not node.ssh_key and not node.ssh_password:
                return Response({
                    'error': f'Node {node.name} has no SSH credentials.',
                    'hint': 'Add SSH key or password to the ManagedServer record to enable remote scaling.',
                }, status=400)
            ssh = SSHClient(
                ip=node.host,
                key_content=node.ssh_key,
                password=node.ssh_password,
                user=getattr(node, 'ssh_user', 'root') or 'root',
                port=getattr(node, 'ssh_port', 22) or 22,
                key_passphrase=getattr(node, 'ssh_key_passphrase', '') or '',
                wg_address=getattr(node, 'wg_address', '') or '',
            )
            try:
                cmd = f"docker update {update_cmd} {container_name}"
                stdout, stderr, exit_code = ssh.exec_command(cmd, timeout=60)
                if exit_code == 0:
                    return Response({'status': 'applied', 'node': node.name, 'container': container_name})
                return Response({'error': f'docker update failed: {stderr}'}, status=500)
            except Exception as exc:
                return Response({'error': f'SSH failed: {exc}'}, status=500)
            finally:
                ssh.close()
        else:
            try:
                client = docker_lib.from_env()
                container = client.containers.get(container_name)
                update_kwargs = {}
                if memory and memory > 0:
                    update_kwargs['mem_reservation'] = f"{memory}m"
                    update_kwargs['mem_limit'] = f"{int(memory * ceiling)}m"
                if cpu and cpu > 0:
                    update_kwargs['cpu_shares'] = max(2, int((cpu / 1000) * 1024))
                    update_kwargs['cpu_period'] = 100000
                    update_kwargs['cpu_quota'] = int((cpu / 1000) * 100000 * ceiling)
                container.update(**update_kwargs)
                return Response({'status': 'applied', 'container': container_name})
            except docker_lib.errors.NotFound:
                return Response({'error': f'Container {container_name} not found locally'}, status=404)
            except Exception as exc:
                return Response({'error': str(exc)}, status=500)
