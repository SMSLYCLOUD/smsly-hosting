from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from .models import Service, Deployment
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer
from .tasks import smart_deploy_task
from apps.cloud.models import CloudProvider
import os
import uuid

class ServiceViewSet(viewsets.ModelViewSet):
    """
    Legacy Service Management.
    """
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

class DeploymentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing Deployments.
    """
    queryset = Deployment.objects.all()
    serializer_class = DeploymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser]  # Enable File Uploads

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        """
        Trigger a new deployment.
        POST /api/v1/deployments/trigger/
        Body: { "service_id": "uuid", "provider_id": "uuid" }
        """
        serializer = DeploymentTriggerSerializer(data=request.data)
        if serializer.is_valid():
            service_id = serializer.validated_data['service_id']
            provider_id = serializer.validated_data['provider_id']

            try:
                service = Service.objects.get(id=service_id)
                provider = CloudProvider.objects.get(id=provider_id)

                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=serializer.validated_data.get('commit_hash', 'latest')
                )

                smart_deploy_task.delay(str(deployment.id), str(provider.id))

                return Response({
                    'message': 'Deployment triggered successfully',
                    'deployment_id': deployment.id,
                    'status': deployment.status
                }, status=status.HTTP_201_CREATED)

            except (Service.DoesNotExist, CloudProvider.DoesNotExist):
                return Response({'error': 'Resource not found'}, status=status.HTTP_404_NOT_FOUND)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='upload')
    def upload_source(self, request):
        """
        Upload source code (zip) for CLI deployment.
        POST /api/v1/deployments/upload/
        Form Data:
          - service_id: UUID
          - file: source.zip
        """
        service_id = request.data.get('service_id')
        uploaded_file = request.FILES.get('file')
        
        if not service_id or not uploaded_file:
            return Response({'error': 'Missing service_id or file'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            service = Service.objects.get(id=service_id)

            # Save file to temp location
            # In prod, this should go to S3. For "LocalAdapter", local FS is fine.
            upload_dir = "/tmp/uploads"
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, f"{service_id}_{uuid.uuid4().hex[:8]}.zip")

            with open(file_path, 'wb+') as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            # Update Service to point to this file (simulated "UPLOAD" mode)
            # In a real impl, we'd add 'source_path' to deployment
            service.deploy_type = 'UPLOAD'
            service.repository_url = f"file://{file_path}"
            service.save()

            # Trigger Deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_message="CLI Upload"
            )

            # If no provider set on service, find default
            provider_id = str(service.provider.id) if service.provider else None
            # Fallback (Hack)
            if not provider_id:
                 provider_id = str(CloudProvider.objects.first().id)

            smart_deploy_task.delay(str(deployment.id), provider_id)

            return Response({'message': 'Source uploaded and deployment triggered', 'deployment_id': deployment.id})

        except Service.DoesNotExist:
            return Response({'error': 'Service not found'}, status=status.HTTP_404_NOT_FOUND)
