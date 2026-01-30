from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Service

class TopologyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]  # SECURITY: Require authentication

    def list(self, request):
        """
        Build topology graph showing only services owned by the requesting user.
        
        SECURITY: Zero Trust - Only show user's own services in topology.
        """
        # Filter services to only those owned by the current user
        user_services = Service.objects.filter(owner=request.user).prefetch_related('addons', 'volumes')
        
        # Build graph with user's services only
        nodes = []
        edges = []
        
        for service in user_services:
            # Add service node
            nodes.append({
                'id': str(service.id),
                'type': 'service',
                'data': {
                    'name': service.name,
                    'port': service.internal_port,
                    'replicas': service.min_replicas,
                }
            })
            
            # Add addon nodes and edges
            for addon in service.addons.all():
                addon_id = f"addon-{addon.id}"
                nodes.append({
                    'id': addon_id,
                    'type': 'addon',
                    'data': {
                        'name': addon.name,
                        'addon_type': addon.addon_type,
                        'status': addon.status,
                    }
                })
                edges.append({
                    'source': str(service.id),
                    'target': addon_id,
                })
            
            # Add volume nodes and edges
            for volume in service.volumes.all():
                volume_id = f"volume-{volume.id}"
                nodes.append({
                    'id': volume_id,
                    'type': 'volume',
                    'data': {
                        'mount_path': volume.mount_path,
                        'size_gb': volume.size_gb,
                    }
                })
                edges.append({
                    'source': str(service.id),
                    'target': volume_id,
                })
        
        return Response({'nodes': nodes, 'edges': edges})
