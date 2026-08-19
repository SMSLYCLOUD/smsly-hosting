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


class TopologyViewSet(viewsets.GenericViewSet):
    serializer_class = TopologySerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [TopologyListRateThrottle]

    def list(self, request):
        """
        Build topology graph with rich node/edge data for canvas rendering.

        SECURITY: Enforces Hybrid RBAC — strictly linked to project.
        """
        from django.db.models import Q

        project_id = request.query_params.get('project_id')

        # Build base queryset with RBAC
        qs = Service.objects.filter(
            Q(owner=request.user) | Q(project__team__members__user=request.user)
        )

        if project_id:
            qs = qs.filter(project_id=project_id)

        user_services = qs.distinct().prefetch_related(
            'addons', 'volumes', 'env_vars',
            'cron_jobs', 'replicas',
        )

        service_ids = [s.id for s in user_services]
        latest_per_service = {}
        if service_ids:
            deployments = (
                Deployment.objects
                .filter(service_id__in=service_ids)
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
            for addon in service.addons.all():
                addon_id = f"addon-{addon.id}"
                addon_upper = (addon.addon_type or '').upper()
                if addon_upper in ('POSTGRES', 'MYSQL', 'MONGODB',
                                   'MARIADB', 'CLICKHOUSE'):
                    addon_kind = 'DATABASE'
                elif addon_upper in ('REDIS', 'MEMCACHED'):
                    addon_kind = 'CACHE'
                elif addon_upper in ('RABBITMQ', 'KAFKA'):
                    addon_kind = 'QUEUE'
                elif addon_upper in {'ELASTICSEARCH', 'QDRANT'}:
                    addon_kind = 'SEARCH'
                elif addon_upper == 'MINIO':
                    addon_kind = 'STORAGE'
                else:
                    addon_kind = 'STORAGE'

                nodes.append({
                    'id': addon_id,
                    'type': 'addon',
                    'data': {
                        'name': addon.name,
                        'label': f"{addon.name} ({addon.addon_type})",
                        'status': addon.status,
                        'kind': addon_kind,
                        'subtype': addon.addon_type,
                        'region': '',
                        'addon_type': addon.addon_type,
                    }
                })

                link_type = addon_kind if addon_kind != 'STORAGE' else 'ADDON'
                edges.append({
                    'id': _edge_id(),
                    'source': svc_id,
                    'target': addon_id,
                    'type': link_type,
                    'label': addon.addon_type,
                })

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

        for service in user_services:
            svc_id = str(service.id)
            for var in service.env_vars.all():
                val = var.value or ''
                if not val:
                    continue

                for other in user_services:
                    if other.id == service.id:
                        continue

                    is_match = False
                    match_type = "API"
                    evidence = ""

                    # 1. Match by Service Name (Standard Heuristic)
                    if re.search(rf'https?://{re.escape(other.name)}', val, re.IGNORECASE):
                        is_match = True
                        match_type = "API"
                        evidence = f"Name match: {other.name}"

                    # 1b. Match by {{SERVICE:name}} placeholder (ecosystem plan format)
                    if not is_match:
                        pattern = (r'\{\{SERVICE\s*:\s*' + re.escape(other.name)
                                   + r'\s*\}\}')
                        if re.search(pattern, val, re.IGNORECASE):
                            is_match = True
                            match_type = "API"
                            evidence = f"SERVICE ref: {other.name}"

                    # 2. Match by Mesh IP (10.10.0.x)
                    if not is_match:
                        # Find other's mesh IP if exists
                        other_peer = WireGuardPeer.objects.filter(
                            server=other.server, is_active=True
                        ).first()
                        if other_peer and other_peer.wg_address in val:
                            is_match = True
                            match_type = "MESH"
                            evidence = f"Mesh IP match: {other_peer.wg_address}"

                    # 3. Match by Private IP (AWS Internal)
                    if not is_match and getattr(other.server, 'private_ip', None):
                        if other.server.private_ip in val:
                            is_match = True
                            match_type = "INTERNAL"
                            evidence = f"Private IP match: {other.server.private_ip}"

                    # 4. Match by Public Domain
                    if not is_match and other.public_domain:
                        if other.public_domain in val:
                            is_match = True
                            match_type = "EXTERNAL"
                            evidence = f"Domain match: {other.public_domain}"

                    if is_match:
                        edges.append({
                            'id': _edge_id(),
                            'source': svc_id,
                            'target': str(other.id),
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
