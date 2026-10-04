"""Views Topology module — enriched topology data for canvas visualization."""
import logging
import re
import uuid

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.rate_limiting import TopologyListRateThrottle

from ..models import (  # type: ignore[attr-defined]    # re-exported via models.py hub; mypy can't see through the empty module.
    Deployment,
    Service,
)

logger = logging.getLogger(__name__)


class TopologySerializer(serializers.Serializer):
    nodes = serializers.JSONField()
    edges = serializers.JSONField()


def _edge_id():
    """Generate a short unique edge ID."""
    return f"e-{uuid.uuid4().hex[:8]}"


def _addon_kind(addon_type: str) -> str:
    t = (addon_type or '').upper()
    if t in ('POSTGRES', 'MYSQL', 'MONGODB', 'MARIADB', 'CLICKHOUSE',
             'TIMESCALEDB', 'PGBOUNCER', 'COCKROACHDB', 'PERCONA', 'VITESS',
             'CASSANDRA', 'SCYLLADB', 'NEO4J', 'DGRAPH', 'INFLUXDB',
             'QUESTDB', 'SURREALDB', 'ARANGODB', 'COUCHDB', 'RETHINKDB',
             'FERRETDB'):
        return 'DATABASE'
    if t in ('REDIS', 'MEMCACHED', 'KEYDB', 'VALKEY', 'DRAGONFLYDB', 'ETCD'):
        return 'CACHE'
    if t in ('RABBITMQ', 'KAFKA', 'NATS', 'REDPANDA', 'PULSAR', 'ACTIVEMQ'):
        return 'QUEUE'
    if t in ('ELASTICSEARCH', 'OPENSEARCH', 'MEILISEARCH', 'TYPESENSE',
             'SOLR', 'QDRANT', 'WEAVIATE', 'MILVUS', 'CHROMADB'):
        return 'SEARCH'
    return 'STORAGE'


def _addon_node(addon, shared: bool = False) -> dict:
    addon_id = f"addon-{addon.id}"
    kind = _addon_kind(getattr(addon, 'addon_type', ''))
    label = f"{addon.name} ({addon.addon_type})"
    if shared:
        label = f"shared · {label}"
    return {
        'id': addon_id,
        'type': 'addon',
        'data': {
            'name': addon.name,
            'label': label,
            'status': addon.status,
            'kind': kind,
            'subtype': addon.addon_type,
            'region': '',
            'addon_type': addon.addon_type,
            'shared': bool(shared),
            'provision_mode': str(getattr(addon, 'provision_mode', '') or ''),
        }
    }


def _addon_edge(svc_id: str, addon, shared: bool = False) -> dict:
    kind = _addon_kind(getattr(addon, 'addon_type', ''))
    link_type = kind if kind != 'STORAGE' else 'ADDON'
    label = str(getattr(addon, 'addon_type', '') or '')
    if shared:
        label = f"{label} · shared"
    return {
        'id': _edge_id(),
        'source': svc_id,
        'target': f"addon-{addon.id}",
        'type': link_type,
        'label': label,
    }


class TopologyViewSet(viewsets.GenericViewSet):
    serializer_class = TopologySerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [TopologyListRateThrottle]

    def list(self, request):
        """
        Build topology graph with rich node/edge data for canvas rendering.

        SECURITY: Enforces Hybrid RBAC — strictly linked to project.
        """
        from django.db.models import Prefetch, Q

        from apps.autoscaler.models.replica import ServiceReplica

        project_id = request.query_params.get('project_id')

        # Build base queryset with RBAC
        qs = Service.objects.filter(
            Q(owner=request.user) | Q(project__team__members__user=request.user)
        )

        if project_id:
            qs = qs.filter(project_id=project_id)

        user_services = qs.distinct().select_related(
            'project', 'server',
        ).prefetch_related(
            'addons', 'volumes', 'env_vars',
            'cron_jobs',
            Prefetch(
                'replicas',
                queryset=ServiceReplica.objects.select_related('node'),
            ),
        )

        service_ids = [s.id for s in user_services]
        latest_per_service = {}
        if service_ids:
            # .only(): rows carry MBs of build/runtime logs — fetch just
            # the columns the graph needs or data transfer grows with log
            # volume instead of graph size (timeout cause).
            deployments = (
                Deployment.objects
                .filter(service_id__in=service_ids)
                .only('id', 'service_id', 'status', 'commit_hash', 'created_at')
                .order_by('service_id', '-created_at')
            )
            seen = set()
            for d in deployments:
                if d.service_id in seen:
                    continue
                seen.add(d.service_id)
                latest_per_service[d.service_id] = d

        nodes = []
        edges = []
        service_ids = set()

        for service in user_services:
            svc_id = str(service.id)
            service_ids.add(svc_id)

            latest_deploy = latest_per_service.get(service.id)

            deploy_status = 'NONE'
            deploy_commit = None
            deploy_time = None
            if latest_deploy:
                deploy_status = latest_deploy.status
                deploy_commit = latest_deploy.commit_hash
                deploy_time = (
                    latest_deploy.created_at.isoformat()
                    if latest_deploy.created_at else None
                )

            # Map deploy status to canonical status
            status = {
                'SUCCESS': 'ACTIVE', 'RUNNING': 'ACTIVE',
                'FAILED': 'FAILED', 'ERROR': 'FAILED',
            }.get(deploy_status, deploy_status)

            # Gather project info if available
            project_id = None
            project_name = None
            if hasattr(service, 'project') and service.project:
                project_id = str(service.project.id)
                project_name = service.project.name

            running_replicas = getattr(service, 'running_replicas', 0)
            nodes.append({
                'id': svc_id,
                'type': 'service',
                'data': {
                    'name': service.name,
                    'label': service.name,
                    'status': status,
                    'kind': 'COMPUTE',
                    'subtype': getattr(
                        service, 'buildpack', 'NIXPACKS'),
                    'region': '',
                    'port': service.internal_port,
                    'replicas': running_replicas,
                    'health': getattr(service, 'health_status', 'unknown'),
                    'domain': getattr(service, 'public_domain', None),
                    'url': getattr(service, 'public_domain', None),
                    'deploy_status': deploy_status,
                    'deploy_commit': deploy_commit,
                    'deploy_time': deploy_time,
                    'build_strategy': getattr(
                        service, 'buildpack', 'NIXPACKS'),
                    'project_id': project_id,
                    'project_name': project_name,
                    'metadata': {
                        'replicas': running_replicas,
                        'port': service.internal_port,
                        'buildpack': getattr(service, 'buildpack', 'NIXPACKS'),
                    },
                }
            })

            # ── Public domain traffic entry node ──────────────────────
            public_domain = getattr(service, 'public_domain', None)
            if public_domain:
                pd_id = f"traffic-{svc_id}"
                nodes.append({
                    'id': pd_id,
                    'type': 'domain',
                    'data': {
                        'name': public_domain,
                        'label': public_domain,
                        'kind': 'EXTERNAL',
                        'subtype': 'DOMAIN',
                        'status': 'ACTIVE',
                        'region': '',
                    }
                })
                edges.append({
                    'id': _edge_id(),
                    'source': pd_id,
                    'target': svc_id,
                    'type': 'DOMAIN',
                    'label': 'traffic entry',
                })

            # ── Replica nodes ─────────────────────────────────────────
            for replica in service.replicas.all():
                if replica.status not in ('RUNNING', 'SPAWNING', 'DRAINING'):
                    continue
                replica_id = f"replica-{replica.id}"
                replica_status = {
                    'RUNNING': 'ACTIVE',
                    'SPAWNING': 'BUILDING',
                    'DRAINING': 'STOPPED',
                }.get(replica.status, 'UNKNOWN')
                node_name = replica.node.name if replica.node else 'local'
                nodes.append({
                    'id': replica_id,
                    'type': 'replica',
                    'data': {
                        'name': replica.container_name,
                        'label': f"{service.name}-replica",
                        'status': replica_status,
                        'kind': 'COMPUTE',
                        'subtype': 'REPLICA',
                        'region': '',
                        'node': node_name,
                        'spawn_reason': replica.spawn_reason,
                        'metrics': replica.metrics_snapshot,
                        'created_at': replica.created_at.isoformat() if replica.created_at else None,
                    }
                })
                edges.append({
                    'id': _edge_id(),
                    'source': svc_id,
                    'target': replica_id,
                    'type': 'REPLICA',
                    'label': f"→ {node_name}",
                })

            # ── Addon nodes + edges ──────────────────────────────────
            # Own addons first; project `*-shared` fallbacks fill types
            # the service lacks (same rule the deploy env uses:
            # service's own addons win, `{type}-shared` in the same
            # project backs DATABASE_URL/REDIS_URL). Without this, a
            # service on a shared addon shows no database at all.
            covered_types: set[str] = set()
            for addon in service.addons.all():
                covered_types.add(str(addon.addon_type or '').upper())
                nodes.append(_addon_node(addon, shared=False))
                edges.append(_addon_edge(svc_id, addon, shared=False))

            shared_owner_ids = {str(s.id) for s in user_services}
            if project_id:
                from apps.deployments.models.addons import Addon as _Addon
                for addon in _Addon.objects.filter(
                    service__project_id=service.project_id,
                    status='ACTIVE',
                    name__endswith='-shared',
                ).exclude(service=service).only(
                    'id', 'name', 'addon_type', 'status', 'service_id',
                    'provision_mode',
                ):
                    if str(addon.addon_type or '').upper() in covered_types:
                        continue
                    if str(addon.service_id) not in shared_owner_ids:
                        continue  # fail-closed: only render visible services' addons
                    covered_types.add(str(addon.addon_type or '').upper())
                    nodes.append(_addon_node(addon, shared=True))
                    edges.append(_addon_edge(svc_id, addon, shared=True))

            # ── Volume nodes + edges ─────────────────────────────────
            for volume in service.volumes.all():
                volume_id = f"volume-{volume.id}"
                vol_name = getattr(volume, 'name', volume.mount_path)
                nodes.append({
                    'id': volume_id,
                    'type': 'volume',
                    'data': {
                        'name': vol_name,
                        'label': f"{vol_name} ({volume.size_gb}GB)",
                        'kind': 'STORAGE',
                        'subtype': 'VOLUME',
                        'mount_path': volume.mount_path,
                        'size_gb': volume.size_gb,
                        'status': 'ACTIVE',
                        'region': '',
                    }
                })
                edges.append({
                    'id': _edge_id(),
                    'source': svc_id,
                    'target': volume_id,
                    'type': 'STORAGE',
                    'label': volume.mount_path,
                })

            # ── Custom domain nodes + edges ──────────────────────────
            # custom_domains is a JSONField (list of strings), not a relation
            domains_list = getattr(service, 'custom_domains', None) or []
            for idx, domain_str in enumerate(domains_list):
                if not domain_str:
                    continue
                domain_id = f"domain-{svc_id}-{idx}"
                nodes.append({
                    'id': domain_id,
                    'type': 'domain',
                    'data': {
                        'name': domain_str,
                        'label': domain_str,
                        'kind': 'EXTERNAL',
                        'subtype': 'DOMAIN',
                        'status': 'ACTIVE',
                        'region': '',
                    }
                })
                edges.append({
                    'id': _edge_id(),
                    'source': domain_id,
                    'target': svc_id,
                    'type': 'DOMAIN',
                    'label': 'routes to',
                })

            # ── Cron job nodes + edges ───────────────────────────────
            if hasattr(service, 'cron_jobs'):
                for cron in service.cron_jobs.all():
                    cron_id = f"cron-{cron.id}"
                    nodes.append({
                        'id': cron_id,
                        'type': 'cron',
                        'data': {
                            'name': cron.name,
                            'label': f"{cron.name} ({cron.schedule})",
                            'kind': 'COMPUTE',
                            'subtype': 'CRON',
                            'schedule': cron.schedule,
                            'command': cron.command,
                            'status': 'ACTIVE' if getattr(cron, 'enabled', True) else 'STOPPED',
                            'region': '',
                        }
                    })
                    edges.append({
                        'id': _edge_id(),
                        'source': svc_id,
                        'target': cron_id,
                        'type': 'CRON',
                        'label': cron.schedule,
                    })

        # ── Tunnel nodes + edges ─────────────────────────────────────
        try:
            from ..models.tunnels import Tunnel
            tunnels = Tunnel.objects.filter(
                owner=request.user, is_active=True
            )
            for tunnel in tunnels:
                # Tunnel doesn't directly link to a service in the model,
                # but we can show it as a standalone external node.
                # Only include it in this project's topology if it maps to a service here.
                matched_service = None
                for service in user_services:
                    if service.internal_port == tunnel.local_port:
                        matched_service = service
                        break

                if matched_service:
                    tunnel_id = f"tunnel-{tunnel.id}"
                    nodes.append({
                        'id': tunnel_id,
                        'type': 'tunnel',
                        'data': {
                            'name': tunnel.subdomain or f"tunnel-{tunnel.local_port}",
                            'label': f":{tunnel.local_port} → {tunnel.subdomain or 'auto'}.tunnel",
                            'kind': 'EXTERNAL',
                            'subtype': 'TUNNEL',
                            'status': 'ACTIVE',
                            'region': '',
                            'public_url': tunnel.public_url,
                            'local_port': tunnel.local_port,
                        }
                    })
                    edges.append({
                        'id': _edge_id(),
                        'source': tunnel_id,
                        'target': str(matched_service.id),
                        'type': 'TUNNEL',
                        'label': f":{tunnel.local_port}",
                    })
        except Exception as e:
            logger.debug("Tunnels not available for topology: %s", e)

        # ── Inter-service dependencies from env vars + Mesh IPs ─────────────────
        from apps.deployments.models.mesh import WireGuardPeer

        # Bulk-fetch mesh state ONCE: the per-pair query this loop used to
        # run made the endpoint O(S²·V) in DB hits (timeout at scale).
        # select_related('server') above means no query per service here.
        _mesh_by_server: dict = {}
        _server_ids = [s.server_id for s in user_services if s.server_id]
        if _server_ids:
            for _peer in WireGuardPeer.objects.filter(
                server_id__in=_server_ids, is_active=True,
            ).only('server_id', 'wg_address'):
                _mesh_by_server.setdefault(_peer.server_id, _peer.wg_address)

        # Precompile per-target matchers once instead of per (service, var)
        # pair. Match order is unchanged: name → SERVICE ref → mesh IP →
        # private IP → public domain.
        _targets = []
        for other in user_services:
            _targets.append({
                'id': other.id,
                'name': other.name,
                'name_re': re.compile(
                    rf'https?://{re.escape(other.name)}', re.IGNORECASE),
                'ref_re': re.compile(
                    r'\{\{SERVICE\s*:\s*' + re.escape(other.name)
                    + r'\s*\}\}', re.IGNORECASE),
                'mesh_ip': _mesh_by_server.get(other.server_id),
                'private_ip': getattr(other.server, 'private_ip', None)
                if other.server_id else None,
                'public_domain': other.public_domain,
            })

        for service in user_services:
            svc_id = str(service.id)
            for var in service.env_vars.all():
                val = var.value or ''
                if not val:
                    continue
                # Cap scanned text: values can be certs/JSON blobs and the
                # heuristics only need the head.
                haystack = val[:2000]

                for target in _targets:
                    if target['id'] == service.id:
                        continue

                    is_match = False
                    match_type = "API"
                    evidence = ""

                    # 1. Match by Service Name (Standard Heuristic)
                    if target['name_re'].search(haystack):
                        is_match = True
                        match_type = "API"
                        evidence = f"Name match: {target['name']}"

                    # 1b. Match by {{SERVICE:name}} placeholder (ecosystem plan format)
                    if not is_match and target['ref_re'].search(haystack):
                        is_match = True
                        match_type = "API"
                        evidence = f"SERVICE ref: {target['name']}"

                    # 2. Match by Mesh IP (10.10.0.x)
                    if not is_match and target['mesh_ip'] and target['mesh_ip'] in haystack:
                        is_match = True
                        match_type = "MESH"
                        evidence = f"Mesh IP match: {target['mesh_ip']}"

                    # 3. Match by Private IP (AWS Internal)
                    if not is_match and target['private_ip']:
                        if target['private_ip'] in haystack:
                            is_match = True
                            match_type = "INTERNAL"
                            evidence = f"Private IP match: {target['private_ip']}"

                    # 4. Match by Public Domain
                    if not is_match and target['public_domain']:
                        if target['public_domain'] in haystack:
                            is_match = True
                            match_type = "EXTERNAL"
                            evidence = f"Domain match: {target['public_domain']}"

                    if is_match:
                        edges.append({
                            'id': _edge_id(),
                            'source': svc_id,
                            'target': str(target['id']),
                            'type': match_type,
                            'label': var.key,
                            'data': {
                                'protocol': 'HTTP/Mesh',
                                'evidence': evidence,
                                'var_key': var.key,
                            },
                        })

        return Response({'nodes': nodes, 'edges': edges})

    @action(detail=False, methods=['get'], url_path='ecosystem')
    def ecosystem(self, request):
        """Return the full platform infrastructure ecosystem topology graph.

        SECURITY: Admin-only — the ecosystem graph contains every
        service, addon, mesh peer, and replication relationship in
        the platform. Restricting to ``IsAdminUser`` prevents regular
        users from enumerating other tenants' infrastructure.
        """
        if not request.user or not request.user.is_authenticated or not request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Admin access required.")
        from ..services.ecosystem_graph_builder import EcosystemGraphBuilder
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        return Response(graph)
