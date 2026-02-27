"""Views Topology module — enriched topology data for canvas visualization."""
from rest_framework import serializers, viewsets
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Service, Deployment


class TopologySerializer(serializers.Serializer):
    nodes = serializers.JSONField()
    edges = serializers.JSONField()


class TopologyViewSet(viewsets.GenericViewSet):
    serializer_class = TopologySerializer
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """
        Build topology graph with rich node/edge data for canvas rendering.

        SECURITY: Zero Trust — only show user's own services.
        """
        user_services = Service.objects.filter(
            owner=request.user
        ).prefetch_related('addons', 'volumes', 'env_vars')

        nodes = []
        edges = []
        service_ids = set()

        for service in user_services:
            service_ids.add(str(service.id))

            # Get latest deployment status
            latest_deploy = Deployment.objects.filter(
                service=service
            ).order_by('-created_at').first()

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

            # Map deploy status to a canonical status for the frontend
            status = {
                'SUCCESS': 'ACTIVE', 'RUNNING': 'ACTIVE',
                'FAILED': 'FAILED', 'ERROR': 'FAILED',
            }.get(deploy_status, deploy_status)

            nodes.append({
                'id': str(service.id),
                'type': 'service',
                'data': {
                    'name': service.name,
                    'status': status,  # Used by Solar System view
                    'kind': 'COMPUTE',
                    'subtype': getattr(
                        service, 'build_strategy', 'DOCKERFILE'),
                    'region': '',
                    'port': service.internal_port,
                    'replicas': getattr(service, 'min_replicas', 1),
                    'health': getattr(service, 'health_status', 'unknown'),
                    'domain': getattr(service, 'public_domain', None),
                    'url': getattr(service, 'public_domain', None),
                    'deploy_status': deploy_status,
                    'deploy_commit': deploy_commit,
                    'deploy_time': deploy_time,
                    'build_strategy': getattr(
                        service, 'build_strategy', 'DOCKERFILE'),
                    'metadata': {
                        'replicas': getattr(service, 'min_replicas', 1),
                        'port': service.internal_port,
                    },
                }
            })

            # Addon nodes + edges
            for addon in service.addons.all():
                addon_id = f"addon-{addon.id}"
                # Map addon_type to kind for frontend Solar System view
                addon_upper = (addon.addon_type or '').upper()
                if addon_upper in ('POSTGRES', 'MYSQL', 'MONGODB'):
                    addon_kind = 'DATABASE'
                elif addon_upper == 'REDIS':
                    addon_kind = 'CACHE'
                elif addon_upper in ('RABBITMQ', 'KAFKA'):
                    addon_kind = 'QUEUE'
                elif addon_upper == 'ELASTICSEARCH':
                    addon_kind = 'SEARCH'
                else:
                    addon_kind = 'STORAGE'

                nodes.append({
                    'id': addon_id,
                    'type': 'addon',
                    'data': {
                        'name': addon.name,
                        'status': addon.status,
                        'kind': addon_kind,  # Used by Solar System view
                        'subtype': addon.addon_type,
                        'region': '',
                        'addon_type': addon.addon_type,
                    }
                })

                # Reuse addon_kind for link type (they map identically)
                link_type = addon_kind if addon_kind != 'STORAGE' else 'ADDON'

                edges.append({
                    'source': str(service.id),
                    'target': addon_id,
                    'type': link_type,
                })

            # Volume nodes + edges
            for volume in service.volumes.all():
                volume_id = f"volume-{volume.id}"
                nodes.append({
                    'id': volume_id,
                    'type': 'volume',
                    'data': {
                        'name': getattr(volume, 'name', volume.mount_path),
                        'mount_path': volume.mount_path,
                        'size_gb': volume.size_gb,
                    }
                })
                edges.append({
                    'source': str(service.id),
                    'target': volume_id,
                    'type': 'STORAGE',
                })

        # Detect inter-service dependencies from env vars
        import re
        for service in user_services:
            svc_id = str(service.id)
            for var in service.env_vars.all():
                val = var.value or ''
                for other in user_services:
                    if other.id == service.id:
                        continue
                    if re.search(
                        rf'https?://{re.escape(other.name)}',
                        val, re.IGNORECASE
                    ):
                        edges.append({
                            'source': svc_id,
                            'target': str(other.id),
                            'type': 'API',
                        })

        return Response({'nodes': nodes, 'edges': edges})
