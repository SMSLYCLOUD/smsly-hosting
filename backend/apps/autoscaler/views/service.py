"""Auto-scaling API: analyze services and manage replicas."""
import logging

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models.core import ManagedServer, Service
from apps.autoscaler.models.replica import ServiceReplica
from apps.deployments.services.node_scorer import NodeScorer, _get_min_score
from apps.deployments.services.spawning_service import SpawningService
from apps.teams.permissions import assert_can_write, get_team_q_filter

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

    def validate_webhook_url(self, value):
        url = str(value or '').strip()
        if not url:
            return url
        from apps.core.validators import validate_ssrf
        validate_ssrf(url, fail_closed_dns=True)
        return url


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
        assert_can_write(request.user, service, action='update autoscaler alert config')
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
            mode=horizontal  — home server only: the master when the
                service lives on the master, or the service's own node
                when it lives on a node (same-server scaling, no extra
                hop). No SSH needed for master; node SSH for node homes.
            mode=vertical    — remote only (a different server, requires SSH)
            (default)        — home first, then remote fallback
        """
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(
                Service, get_team_q_filter(request.user, request=request), id=pk
            )
        assert_can_write(request.user, service, action='spawn service replica')

        mode = request.query_params.get('mode', '').lower() or request.data.get('mode', '').lower() or ''

        spawner = SpawningService()

        # Home server for horizontal scaling: the service's effective
        # server (node when the service lives on a node, else local).
        home_server = None
        try:
            from apps.deployments.services.caddy_manager.config_generation import (
                _resolve_effective_server,
            )
            home_server = _resolve_effective_server(service)
        except Exception:
            home_server = getattr(service, 'server', None)
        home_is_node = bool(
            home_server is not None and not getattr(home_server, 'is_primary', True)
        )

        def _claim_replica_slot(target_node, reason: str):
            with transaction.atomic():
                locked_service = Service.objects.select_for_update().get(id=service.id)
                active_count = ServiceReplica.objects.filter(
                    service=locked_service, status__in=['RUNNING', 'SPAWNING']
                ).count()
                if active_count >= (locked_service.max_replicas or 1):
                    return None, Response({
                        'error': f'At max_replicas ({locked_service.max_replicas}) — cannot spawn more.',
                    }, status=400)
                rep = ServiceReplica.objects.create(
                    service=locked_service, node=target_node, status='SPAWNING',
                    spawn_reason=reason,
                )
                return rep, None

        # --- Horizontal: home server only ---
        if mode != 'vertical':
            if home_is_node:
                replica, cap_err = _claim_replica_slot(
                    home_server, 'Manual spawn via API (horizontal, on home node)'
                )
                if cap_err is not None:
                    return cap_err
                try:
                    spawner.spawn(service, home_server, replica)
                    return Response(ServiceReplicaSerializer(replica).data)
                except Exception as exc:
                    logger.warning("Node-home spawn failed for %s on %s: %s", service.name, home_server.name, exc)
                    replica.status = 'DESTROYED'
                    replica.save(update_fields=['status'])
                    if mode == 'horizontal':
                        return Response({
                            'error': f'Node spawn failed on {home_server.name}: {exc}',
                            'hint': 'The replica spawns on the service\u2019s own node. Check node capacity and SSH health.',
                        }, status=500)
                    # else: fall through to remote
                finally:
                    spawner.cleanup()
            else:
                replica, cap_err = _claim_replica_slot(
                    None, 'Manual spawn via API (horizontal)'
                )
                if cap_err is not None:
                    return cap_err
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
                finally:
                    spawner.cleanup()

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

        replica, cap_err = _claim_replica_slot(best, 'Manual spawn via API (vertical)')
        if cap_err is not None:
            return cap_err
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
                ServiceReplica, id=replica_id, status__in=['RUNNING', 'SPAWNING', 'DRAINING', 'DESTROYING'],
            )
        else:
            replica = get_object_or_404(
                ServiceReplica,
                get_team_q_filter(request.user, prefix='service__', request=request),
                id=replica_id, status__in=['RUNNING', 'SPAWNING', 'DRAINING', 'DESTROYING'],
            )
        assert_can_write(request.user, replica.service, action='destroy service replica')
        spawner = SpawningService()
        try:
            spawner.destroy(replica)
            return Response({'status': 'destroyed', 'replica_id': str(replica.id)})
        finally:
            spawner.cleanup()

    @action(detail=True, methods=['post'])
    def scale_down(self, request, pk=None):
        """Manually scale down a service by 1 replica (or by ?count=N)."""
        if request.user.is_superuser:
            service = get_object_or_404(Service, id=pk)
        else:
            service = get_object_or_404(
                Service, get_team_q_filter(request.user, request=request), id=pk
            )
        assert_can_write(request.user, service, action='scale down service')
        try:
            count = int(request.query_params.get('count', 1) or request.data.get('count', 1))
        except (ValueError, TypeError):
            count = 1
        count = max(1, min(count, 10))

        from apps.autoscaler.engine.decision import Recommendation
        from apps.autoscaler.engine.reconciler import Reconciler

        rec = Recommendation(
            action='scale_down',
            scale_down_by=count,
            reason='Manual scale down via API',
        )
        result = Reconciler(service).apply(rec)
        return Response(result.to_dict())

    @action(detail=True, methods=['post'])
    def apply_vpa(self, request, pk=None):
        """Apply VPA resource limits to a service's running container(s).

        Resolution order for remote nodes:
        1. SSH (if node has ssh_key or ssh_password stored)
        2. Node API call (for self-provisioned/bootstrap nodes with no SSH)
        3. Local Docker SDK (if service runs on this server)
        """
        import json as _json
        import shlex
        import docker as docker_lib
        from apps.autoscaler.services.tasks_autoscale import (
            build_vpa_resource_spec,
            service_vpa_target_containers,
        )
        from apps.deployments.services.ssh_client import SSHClient

        scope_qs = (
            Service.objects.all()
            if request.user.is_superuser
            else Service.objects.filter(get_team_q_filter(request.user, request=request))
        )
        service = scope_qs.filter(id=pk).first()
        if service is None:
            svc_name = str(
                (request.data.get('service_name') if isinstance(request.data, dict) else '')
                or request.query_params.get('service_name', '')
                or ''
            ).strip()
            if svc_name:
                service = scope_qs.filter(name=svc_name).first()
        if service is None:
            service = get_object_or_404(scope_qs, id=pk)
        assert_can_write(request.user, service, action='apply VPA limits')

        spec = build_vpa_resource_spec(service)
        update_parts = spec['update_parts']
        docker_kwargs = spec['docker_kwargs']
        if not update_parts:
            return Response({'error': 'No memory/cpu settings to apply'}, status=400)

        update_cmd = " ".join(update_parts)
        replica_names = service_vpa_target_containers(service)

        if service.server_id:
            node = service.server

            # Path 1: SSH (if credentials available)
            if node.ssh_key or node.ssh_password:
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
                    applied_cnt = 0
                    last_err = ''
                    for target_name in replica_names:
                        cmd = f"docker update {update_cmd} {shlex.quote(target_name)}"
                        _stdout, stderr, exit_code = ssh.exec_command(cmd, timeout=60)
                        if exit_code == 0:
                            applied_cnt += 1
                        else:
                            last_err = stderr
                    if applied_cnt > 0:
                        return Response({'status': 'applied', 'method': 'ssh', 'node': node.name, 'updated': applied_cnt})
                    return Response({'error': f'docker update failed: {last_err}'}, status=500)
                except Exception as exc:
                    return Response({'error': f'SSH failed: {exc}'}, status=500)
                finally:
                    ssh.close()

            # Path 2: Node API (for bootstrap/self-provisioned nodes)
            node_api_url = None
            if getattr(node, 'wg_address', None) and node.api_token:
                node_api_url = f"http://{node.wg_address}:8000"
            elif node.host and node.api_token:
                node_api_url = f"http://{node.host}:8000"

            if node_api_url and node.api_token:
                import requests
                from apps.deployments.views.server.helpers import _build_remote_headers
                try:
                    payload_bytes = _json.dumps({'service_name': service.name}).encode('utf-8')
                    headers = _build_remote_headers(
                        node, method='POST',
                        path=f'/api/v1/autoscaler/services/{service.id}/apply_vpa/',
                        body=payload_bytes,
                    )
                    headers['Content-Type'] = 'application/json'
                    resp = requests.post(
                        f"{node_api_url}/api/v1/autoscaler/services/{service.id}/apply_vpa/",
                        headers=headers,
                        data=payload_bytes,
                        timeout=30,
                    )
                    if resp.ok:
                        return Response({**resp.json(), 'method': 'node_api', 'node': node.name})
                    return Response({
                        'error': f'Node API returned {resp.status_code}: {resp.text}',
                        'hint': 'Ensure the node agent is running and reachable.',
                    }, status=resp.status_code)
                except requests.exceptions.ConnectionError:
                    return Response({
                        'error': f'Cannot reach node {node.name} API.',
                        'hint': 'Node may be offline or firewall blocks port 8000.',
                    }, status=502)
                except Exception as exc:
                    return Response({'error': f'Node API call failed: {exc}'}, status=500)

            return Response({
                'error': f'Node {node.name} has no SSH credentials and no API token.',
                'hint': 'Add SSH key/password to ManagedServer, or ensure agent is registered with api_token.',
            }, status=400)
        else:
            # Local container + replicas
            try:
                client = docker_lib.from_env()
                applied_names: list[str] = []
                for target_name in replica_names:
                    try:
                        container = client.containers.get(target_name)
                    except docker_lib.errors.NotFound:
                        continue
                    container.update(**docker_kwargs)
                    applied_names.append(target_name)
                if not applied_names:
                    return Response({'error': f'Container {service.name} not found locally'}, status=404)
                return Response({'status': 'applied', 'method': 'local', 'containers': applied_names, 'updated': len(applied_names)})
            except Exception as exc:
                return Response({'error': str(exc)}, status=500)
