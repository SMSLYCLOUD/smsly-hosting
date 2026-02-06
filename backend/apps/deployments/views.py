"""Views module."""
from rest_framework import viewsets, permissions, status, parsers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.conf import settings
from .models import Service, Deployment, EnvironmentVariable
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer, EnvVarSerializer
from .tasks import smart_deploy_task
from apps.cloud.models import CloudProvider
import os
import uuid


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Service Management and Nested Resources.
    """
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # --- Nested Resources: Deployments ---
    @action(detail=True, methods=['get'])
    def deployments(self, request, pk=None):
        service = self.get_object()
        deployments = service.deployments.all().order_by('-created_at')
        page = self.paginate_queryset(deployments)
        if page is not None:
            serializer = DeploymentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DeploymentSerializer(deployments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def deploy(self, request, pk=None):
        """
        Manually trigger deployment for a service.
        POST /api/v1/services/{id}/deploy/
        Body: { "ref": "commit_hash" } (Optional)
        """
        service = self.get_object()
        ref = request.data.get('ref', 'HEAD')

        # Determine provider
        provider = service.provider or CloudProvider.objects.first()
        if not provider:
            return Response({'error': 'No cloud provider configured'},
                            status=status.HTTP_400_BAD_REQUEST)

        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=ref if ref != 'HEAD' else 'latest',
            commit_message=f"Manual Trigger: {ref}"
        )

        smart_deploy_task.delay(str(deployment.id), str(provider.id))
        return Response(DeploymentSerializer(deployment).data)

    # --- Nested Resources: Environment Variables ---
    @action(detail=True, methods=['get'], url_path='env_vars')
    def list_env_vars(self, request, pk=None):
        service = self.get_object()
        vars = service.env_vars.all()
        serializer = EnvVarSerializer(vars, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='env_vars')
    def create_env_var(self, request, pk=None):
        service = self.get_object()
        serializer = EnvVarSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(service=service)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['delete'],
            url_path='env_vars/(?P<var_id>\\d+)')
    def delete_env_var(self, request, pk=None, var_id=None):
        service = self.get_object()
        try:
            var = EnvironmentVariable.objects.get(id=var_id, service=service)
            var.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except EnvironmentVariable.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


class DeploymentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing Deployments.
    """
    queryset = Deployment.objects.all()
    serializer_class = DeploymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [
        parsers.JSONParser,
        parsers.MultiPartParser]  # Enable File Uploads

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """
        Rollback to this specific deployment.
        Effectively triggers a new deployment using the commit hash/image from this one.
        """
        target_deployment = self.get_object()
        service = target_deployment.service

        # Create new deployment record for the rollback
        new_deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=target_deployment.commit_hash,
            commit_message=f"Rollback to {target_deployment.commit_hash[:7]}"
        )

        provider = service.provider or CloudProvider.objects.first()
        if provider:
            smart_deploy_task.delay(str(new_deployment.id), str(provider.id))
            return Response(DeploymentSerializer(
                new_deployment).data, status=status.HTTP_201_CREATED)

        return Response({'error': 'No provider available'},
                        status=status.HTTP_400_BAD_REQUEST)

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
                    commit_hash=serializer.validated_data.get(
                        'commit_hash', 'latest')
                )

                smart_deploy_task.delay(str(deployment.id), str(provider.id))

                return Response({
                    'message': 'Deployment triggered successfully',
                    'deployment_id': deployment.id,
                    'status': deployment.status
                }, status=status.HTTP_201_CREATED)

            except (Service.DoesNotExist, CloudProvider.DoesNotExist):
                return Response({'error': 'Resource not found'},
                                status=status.HTTP_404_NOT_FOUND)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def diagnose(self, request, pk=None):
        """
        Trigger AI diagnosis for a deployment.
        """
        deployment = self.get_object()
        from apps.deployments.tasks_ai import analyze_failure_task

        # Trigger analysis asynchronously
        analyze_failure_task.delay(str(deployment.id))

        return Response({'message': 'Analysis started'})

    @action(detail=False, methods=['post'], url_path='upload')
    def upload_source(self, request):
        """
        Upload source code (zip) for CLI deployment.
        """
        service_id = request.data.get('service_id')
        uploaded_file = request.FILES.get('file')

        if not service_id or not uploaded_file:
            return Response({'error': 'Missing service_id or file'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Security: File size limit (100MB)
        MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
        if uploaded_file.size > MAX_UPLOAD_SIZE:
            size_mb = uploaded_file.size / 1024 / 1024
            return Response(
                {'error': f'File too large. Maximum size is 100MB, got {size_mb:.1f}MB'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        # Security: Validate file extension
        if not uploaded_file.name.endswith('.zip'):
            return Response(
                {'error': 'Invalid file type. Only .zip files are allowed'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            service = Service.objects.get(id=service_id)

            # Security: Verify ownership
            if hasattr(service, 'owner') and service.owner != request.user:
                return Response(
                    {'error': 'Permission denied. You do not own this service'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Security: Use secure upload directory
            import secrets
            # Use settings.MEDIA_ROOT or fallback to /tmp/smsly/uploads
            # Using /var/smsly requires root, which CI/CD runner lacks
            base_dir = getattr(settings, 'MEDIA_ROOT', '/tmp/smsly/uploads')
            upload_dir = os.path.join(base_dir, 'uploads')
            os.makedirs(upload_dir, mode=0o700, exist_ok=True)

            # Generate unpredictable filename
            secure_name = f"{service_id}_{secrets.token_hex(16)}.zip"
            file_path = os.path.join(upload_dir, secure_name)

            with open(file_path, 'wb+') as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            # Set restrictive file permissions
            os.chmod(file_path, 0o600)

            # Update Service to point to this file
            service.deploy_type = 'UPLOAD'
            service.repository_url = f"file://{file_path}"
            service.save()

            # Trigger Deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_message=f"CLI Upload: {uploaded_file.name}"
            )

            # If no provider set on service, find default
            provider_id = str(
                service.provider.id) if service.provider else None
            if not provider_id:
                default_provider = CloudProvider.objects.first()
                if default_provider:
                    provider_id = str(default_provider.id)
                else:
                    return Response(
                        {'error': 'No cloud provider configured'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            smart_deploy_task.delay(str(deployment.id), provider_id)

            return Response({
                'message': 'Source uploaded and deployment triggered',
                'deployment_id': deployment.id,
                'file_size': uploaded_file.size
            }, status=status.HTTP_201_CREATED)

        except Service.DoesNotExist:
            return Response({'error': 'Service not found'},
                            status=status.HTTP_404_NOT_FOUND)
