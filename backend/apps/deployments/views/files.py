import logging
import os
import secrets
import uuid
from pathlib import Path

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.deployments.models import Deployment
from apps.deployments.tasks import smart_deploy_task
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)


class ServiceFileActionsMixin:
    """Mixin providing local file upload and archive deployment actions for ServiceViewSet."""

    @action(detail=True, methods=['post'], url_path='upload-deploy', parser_classes=[MultiPartParser, FormParser])
    def upload_and_deploy(self, request, pk=None):
        """
        Upload a local archive (.zip, .tar.gz, .tgz) and immediately deploy the service.
        """
        service = self.get_object()
        assert_can_write(request.user, service, action='upload archive and deploy')

        uploaded_file = request.FILES.get('file') or request.FILES.get('archive')
        if not uploaded_file:
            return Response(
                {'error': 'No file uploaded. Provide "file" or "archive" in form data.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. Size Limit Check (Default 100MB)
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 100 * 1024 * 1024)
        if uploaded_file.size > max_size:
            size_mb = uploaded_file.size / 1024 / 1024
            max_mb = max_size / 1024 / 1024
            return Response(
                {'error': f'File size ({size_mb:.1f}MB) exceeds maximum limit ({max_mb:.0f}MB).'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        # 2. Extension Check
        filename_lower = uploaded_file.name.lower()
        if not (filename_lower.endswith('.zip') or filename_lower.endswith('.tar.gz') or filename_lower.endswith('.tgz')):
            return Response(
                {'error': 'Invalid file archive format. Allowed extensions: .zip, .tar.gz, .tgz'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2b. Magic byte validation
        from .upload_security import validate_zip_magic, validate_tar_magic
        if filename_lower.endswith('.zip'):
            magic_err = validate_zip_magic(uploaded_file)
        else:
            magic_err = validate_tar_magic(uploaded_file)
        if magic_err:
            return magic_err

        # 2c. Zip-specific checks: zip-slip and zip bomb
        if filename_lower.endswith('.zip'):
            from .upload_security import validate_zip_entries, validate_zip_no_bomb
            is_safe, err_msg = validate_zip_entries(uploaded_file)
            if not is_safe:
                return Response(
                    {'error': f'Unsafe archive: {err_msg}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            bomb_err = validate_zip_no_bomb(uploaded_file)
            if bomb_err:
                return bomb_err

        # 3. Secure Storage Setup
        base_dir = getattr(settings, 'MEDIA_ROOT', '/app/media')
        upload_dir = os.path.join(base_dir, 'uploads', 'services', str(service.id))
        os.makedirs(upload_dir, mode=0o700, exist_ok=True)

        ext = '.zip' if filename_lower.endswith('.zip') else '.tar.gz'
        secure_filename = f"deploy_{uuid.uuid4().hex[:12]}_{secrets.token_hex(8)}{ext}"
        file_path = os.path.join(upload_dir, secure_filename)

        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)

        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass

        # 4. Update Service Deploy Type and Repository URI
        service.deploy_type = 'UPLOAD'
        service.repository_url = Path(file_path).resolve().as_uri()
        service.save(update_fields=['deploy_type', 'repository_url', 'updated_at'])

        # 5. Create Deployment and Dispatch Build Task
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=f"upload-{uuid.uuid4().hex[:16]}",
            commit_message=f"Local Upload Deploy: {uploaded_file.name}"
        )

        provider_id = getattr(service, 'provider_id', 'local')
        smart_deploy_task.delay(str(deployment.id), str(provider_id), skip_review=True)

        logger.info("Service %s triggered local upload deployment %s from file %s", service.id, deployment.id, uploaded_file.name)
        return Response({
            'status': 'queued',
            'deployment_id': str(deployment.id),
            'service_id': str(service.id),
            'service_name': service.name,
            'archive_name': uploaded_file.name,
            'deploy_type': 'UPLOAD'
        }, status=status.HTTP_202_ACCEPTED)
