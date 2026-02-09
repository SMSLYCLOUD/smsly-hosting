"""Views Storage module."""
from rest_framework import viewsets, permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models_storage import Volume
from .models import Service
from apps.cloud.adapters.local import LocalAdapter


class VolumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Volume
        fields = '__all__'


class VolumeViewSet(viewsets.ModelViewSet):
    serializer_class = VolumeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if 'service_pk' in self.kwargs:
            return Volume.objects.filter(service_id=self.kwargs['service_pk'])
        return Volume.objects.none()

    def perform_create(self, serializer):
        service = Service.objects.get(pk=self.kwargs['service_pk'])
        serializer.save(service=service)

    @action(detail=True, methods=['get'])
    def browse(self, request, pk=None, service_pk=None):
        """
        List files in the volume.
        Uses docker exec ls -la on the active container.
        """
        volume = self.get_object()
        path = request.query_params.get('path', volume.mount_path)

        # Security check: Ensure path starts with volume mount path to prevent
        # traversal
        if not path.startswith(volume.mount_path):
            return Response({'error': 'Invalid path'},
                            status=status.HTTP_403_FORBIDDEN)

        service = volume.service
        latest_deploy = service.deployments.filter(status='ACTIVE').first()

        if not latest_deploy or not latest_deploy.container_id:
            return Response({'error': 'No active container'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        adapter = LocalAdapter()
        if adapter.docker_client:
            try:
                container = adapter.docker_client.containers.get(
                    latest_deploy.container_id)
                exit_code, output = container.exec_run(
                    f"ls -la {path}", user="root")

                if exit_code != 0:
                    return Response({'error': 'Failed to list directory', 'details': output.decode(
                    )}, status=status.HTTP_400_BAD_REQUEST)

                # Parse ls -la output into JSON
                files = []
                lines = output.decode('utf-8').splitlines()
                # Skip total line
                if lines and lines[0].startswith('total'):
                    lines = lines[1:]

                for line in lines:
                    parts = line.split()
                    if len(parts) >= 9:
                        files.append({
                            'permissions': parts[0],
                            'user': parts[2],
                            'size': parts[4],
                            'date': f"{parts[5]} {parts[6]} {parts[7]}",
                            'name': " ".join(parts[8:])
                        })

                return Response({'path': path, 'files': files})

            except Exception as e:
                return Response({'error': str(e)},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'files': []})  # Mock if no docker
