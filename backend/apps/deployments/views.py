"""Views module."""
from rest_framework import viewsets, permissions, status, parsers
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.conf import settings
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import (
    ServiceSerializer, DeploymentSerializer,
    DeploymentTriggerSerializer, EnvVarSerializer,
    DeploymentTimelineSerializer, InstantRollbackSerializer,
    AuditLogSerializer
)
from .models_audit import AuditLog
from .tasks import smart_deploy_task
from apps.cloud.models import CloudProvider
import os
import uuid
import logging

logger = logging.getLogger(__name__)


def _resolve_provider_for_service(service: Service):
    """
    Resolve provider for deployment in a fail-closed way.
    - If service has an assigned provider, it must be active.
    - Otherwise, fall back to the first active global provider.
    """
    if service.provider:
        return service.provider if service.provider.is_active else None
    return CloudProvider.objects.filter(is_active=True).first()


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Service Management and Nested Resources.
    """
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """ZH-001 FIX: Only return services owned by the requesting user."""
        qs = Service.objects.prefetch_related('deployments')
        if self.request.user.is_superuser:
            return qs.all().order_by('-created_at')
        return qs.filter(owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance):
        """Stop all deployments and audit-log before deleting the service."""
        # Cancel any active deployments
        instance.deployments.filter(
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.QUEUED,
            ]
        ).update(status=Deployment.Status.CANCELLED, finished_at=timezone.now())

        AuditLog(
            actor=self.request.user.get_username(),
            action='SERVICE_DELETE',
            target=f'Service: {instance.name}',
            metadata={'service_id': str(instance.id), 'service_name': instance.name},
        ).save()

        instance.delete()

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
    def stop(self, request, pk=None):
        """
        Stop a running service.
        POST /api/v1/services/{id}/stop/
        Cancels any active deployments and marks the service as stopped.
        """
        service = self.get_object()

        # Cancel any active/building deployments
        active_deployments = service.deployments.filter(
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.HEALTH_CHECK,
                Deployment.Status.QUEUED,
            ]
        )
        count = active_deployments.update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        # Log the stop action
        AuditLog(
            actor=request.user.get_username(),
            action='SERVICE_STOP',
            target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), 'deployments_cancelled': count},
        ).save()

        return Response({
            'message': f'Service {service.name} stopped',
            'deployments_cancelled': count,
        })

    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        """
        Restart a service by stopping it and triggering a new deployment.
        POST /api/v1/services/{id}/restart/
        """
        service = self.get_object()

        # Stop active deployments first
        service.deployments.filter(
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
            ]
        ).update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        # Create a new deployment
        provider = _resolve_provider_for_service(service)
        if not provider:
            return Response({'error': 'No active cloud provider configured'},
                            status=status.HTTP_400_BAD_REQUEST)

        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash='latest',
            commit_message='Service restart',
        )

        smart_deploy_task.delay(str(deployment.id), str(provider.id))

        AuditLog(
            actor=request.user.get_username(),
            action='SERVICE_RESTART',
            target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), 'deployment_id': str(deployment.id)},
        ).save()

        return Response({
            'message': f'Service {service.name} restarting',
            'deployment': DeploymentSerializer(deployment).data,
        }, status=status.HTTP_201_CREATED)

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
        provider = _resolve_provider_for_service(service)
        if not provider:
            return Response({'error': 'No active cloud provider configured'},
                            status=status.HTTP_400_BAD_REQUEST)

        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=ref if ref != 'HEAD' else 'latest',
            commit_message=f"Manual Trigger: {ref}"
        )

        smart_deploy_task.delay(str(deployment.id), str(provider.id))
        return Response(DeploymentSerializer(deployment).data)

    @action(detail=True, methods=['post'], url_path='instant-rollback')
    def instant_rollback(self, request, pk=None):
        """
        Instantly rollback a service to its last successful deployment.
        POST /api/v1/services/{id}/instant-rollback/
        Body: { "message": "optional reason" } (Optional)

        This is the ONE-CLICK rollback that beats Railway.
        No need to find the deployment ID — just hit this endpoint.
        """
        service = self.get_object()
        serializer = InstantRollbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('message', '')

        # Find the most recent ACTIVE deployment
        last_good = (
            Deployment.objects
            .filter(service=service, status=Deployment.Status.ACTIVE)
            .order_by('-finished_at')
            .first()
        )

        if not last_good:
            return Response(
                {'error': 'No previous successful deployment to rollback to'},
                status=status.HTTP_404_NOT_FOUND)

        # Find the current (latest) deployment to mark as source
        current = (
            Deployment.objects
            .filter(service=service)
            .order_by('-created_at')
            .first()
        )

        # Create rollback deployment
        rollback_msg = f"INSTANT ROLLBACK to {last_good.commit_hash[:7]}"
        if reason:
            rollback_msg += f" — {reason}"

        rollback_deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=last_good.commit_hash,
            commit_message=rollback_msg,
            is_rollback=True,
            rollback_from=current,
        )

        provider = _resolve_provider_for_service(service)
        if provider:
            smart_deploy_task.delay(
                str(rollback_deployment.id), str(provider.id))

        return Response({
            'deployment': DeploymentSerializer(rollback_deployment).data,
            'rolled_back_to': DeploymentSerializer(last_good).data,
            'message': f'Rollback initiated to {last_good.commit_hash[:7]}',
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """
        Deployment timeline for a service — paginated, lightweight.
        GET /api/v1/services/{id}/timeline/
        Query params: ?status=ACTIVE&limit=20
        """
        service = self.get_object()
        deployments = service.deployments.all().order_by('-created_at')

        # Filter by status if requested
        status_filter = request.query_params.get('status')
        if status_filter:
            deployments = deployments.filter(status=status_filter.upper())

        page = self.paginate_queryset(deployments)
        if page is not None:
            serializer = DeploymentTimelineSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DeploymentTimelineSerializer(deployments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Deployment statistics for a service.
        GET /api/v1/services/{id}/stats/

        Returns: total deploys, success rate, avg duration, rollback count.
        """
        service = self.get_object()
        deploys = service.deployments.all()

        total = deploys.count()
        active = deploys.filter(status=Deployment.Status.ACTIVE).count()
        failed = deploys.filter(status=Deployment.Status.FAILED).count()
        rollbacks = deploys.filter(is_rollback=True).count()

        # Average duration of successful deployments
        successful = deploys.filter(
            status=Deployment.Status.ACTIVE,
            started_at__isnull=False,
            finished_at__isnull=False,
        ).annotate(
            duration=ExpressionWrapper(
                F('finished_at') - F('started_at'),
                output_field=DurationField()
            )
        ).aggregate(avg_duration=Avg('duration'))

        avg_seconds = None
        if successful['avg_duration']:
            avg_seconds = successful['avg_duration'].total_seconds()

        success_rate = (active / total * 100) if total > 0 else 0

        return Response({
            'total_deployments': total,
            'active': active,
            'failed': failed,
            'rollbacks': rollbacks,
            'success_rate': round(success_rate, 1),
            'avg_duration_seconds': round(avg_seconds, 1) if avg_seconds else None,
        })

    # --- Nested Resources: Environment Variables ---
    # NOTE: Keep GET and POST on a single @action. DRF collects actions via
    # `inspect.getmembers()` (sorted by name), which can register duplicate
    # url_path patterns in an unexpected order and cause 405s for valid methods.
    @action(detail=True, methods=['get', 'post'], url_path='env_vars')
    def env_vars(self, request, pk=None):
        service = self.get_object()

        if request.method.upper() == 'GET':
            vars = service.env_vars.all().order_by('key')
            serializer = EnvVarSerializer(vars, many=True)
            return Response(serializer.data)

        serializer = EnvVarSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        key = str(serializer.validated_data.get('key') or '').strip()
        value = serializer.validated_data.get('value', '')
        is_secret = bool(serializer.validated_data.get('is_secret', False))

        env_var, created = EnvironmentVariable.objects.update_or_create(
            service=service,
            key=key,
            defaults={'value': value, 'is_secret': is_secret},
        )
        out = EnvVarSerializer(env_var).data
        return Response(
            out,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

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

    @action(detail=True, methods=['post'], url_path='verify-domain')
    def verify_domain(self, request, pk=None):
        """
        Verify that a custom domain's DNS points to cname.cloud.smsly.cloud.
        POST /api/v1/services/{id}/verify-domain/
        Body: { "domain": "myapp.com" }
        """
        import socket
        service = self.get_object()
        domain = request.data.get('domain', '').strip().lower()

        if not domain or '.' not in domain:
            return Response({'error': 'Invalid domain'},
                            status=status.HTTP_400_BAD_REQUEST)

        cname_target = os.getenv('CNAME_TARGET', 'cname.cloud.smsly.cloud')
        try:
            # Check CNAME or A record
            resolved = socket.getaddrinfo(domain, 443)
            target_ips = socket.getaddrinfo(cname_target, 443)

            domain_ips = {r[4][0] for r in resolved}
            expected_ips = {r[4][0] for r in target_ips}

            is_valid = bool(domain_ips & expected_ips)
        except socket.gaierror:
            is_valid = False

        return Response({
            'domain': domain,
            'verified': is_valid,
            'cname_target': cname_target,
            'message': 'DNS verified! Domain points to CloudNeuron.' if is_valid
                       else f'DNS not configured. Add a CNAME record pointing to {cname_target}',
        })


class DeploymentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing Deployments.
    """
    serializer_class = DeploymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [
        parsers.JSONParser,
        parsers.MultiPartParser]  # Enable File Uploads

    def get_queryset(self):
        """ZH-002 FIX: Only return deployments for services owned by the requesting user."""
        if self.request.user.is_superuser:
            return Deployment.objects.all()
        return Deployment.objects.filter(service__owner=self.request.user)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """
        Rollback to this specific deployment.
        Effectively triggers a new deployment using the commit hash/image
        from this one.
        """
        target_deployment = self.get_object()
        service = target_deployment.service

        # Create new deployment record for the rollback
        new_deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=target_deployment.commit_hash,
            commit_message=f"Rollback to {target_deployment.commit_hash[:7]}",
            is_rollback=True,
            rollback_from=target_deployment,
        )

        provider = _resolve_provider_for_service(service)
        if provider:
            smart_deploy_task.delay(str(new_deployment.id), str(provider.id))
            return Response(DeploymentSerializer(
                new_deployment).data, status=status.HTTP_201_CREATED)

        return Response({'error': 'No active provider available'},
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
                # ZH-011 FIX: Verify service ownership before triggering deployment
                service = Service.objects.get(id=service_id, owner=request.user)
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
    def cancel(self, request, pk=None):
        """
        Cancel a queued or building deployment.
        POST /api/v1/deployments/{id}/cancel/
        """
        deployment = self.get_object()

        if deployment.status not in (
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
        ):
            return Response(
                {'error': f'Cannot cancel deployment in {deployment.status} '
                          f'status. Only QUEUED or BUILDING deployments can '
                          f'be cancelled.'},
                status=status.HTTP_409_CONFLICT)

        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs += "\n\n[CANCELLED] Deployment cancelled by user."
        deployment.save()

        return Response(DeploymentSerializer(deployment).data)

    @action(detail=True, methods=['get'], url_path='build-logs')
    def build_logs(self, request, pk=None):
        """
        Get build logs for a deployment (REST fallback for non-WebSocket).
        GET /api/v1/deployments/{id}/build-logs/
        """
        deployment = self.get_object()
        return Response({
            'id': str(deployment.id),
            'status': deployment.status,
            'build_logs': deployment.build_logs,
            'started_at': deployment.started_at,
            'finished_at': deployment.finished_at,
            'duration_seconds': deployment.duration_seconds,
        })

    @action(detail=True, methods=['get'], url_path='runtime-logs')
    def runtime_logs(self, request, pk=None):
        """
        Get live runtime logs from the deployed Docker container.
        GET /api/v1/deployments/{id}/runtime-logs/?tail=200
        """
        deployment = self.get_object()
        tail = int(request.query_params.get('tail', 200))
        tail = min(tail, 1000)  # Cap at 1000 lines

        try:
            import docker
            client = docker.from_env()

            # Find container by service name
            service_name = deployment.service.name
            containers = client.containers.list(
                filters={'name': service_name},
                limit=1,
            )

            if not containers:
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': 'No running container found for this service.',
                })

            container = containers[0]
            logs = container.logs(
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=True,
            )
            log_text = logs.decode('utf-8', errors='replace')

            return Response({
                'id': str(deployment.id),
                'container_id': container.short_id,
                'container_status': container.status,
                'runtime_logs': log_text,
            })

        except ImportError:
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': 'Docker SDK not available.',
            })
        except Exception as e:
            logger.warning("Failed to fetch runtime logs for %s: %s", pk, e)
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': f'Could not fetch runtime logs: {str(e)}',
            })

    @action(detail=True, methods=['post'])
    def diagnose(self, request, pk=None):
        """
        Trigger AI diagnosis for a deployment.
        """
        deployment = self.get_object()
        from apps.deployments.tasks_ai import analyze_failure_task

        # Trigger analysis asynchronously
        try:
            analyze_failure_task.delay(str(deployment.id))
        except Exception:
            # Avoid hard-failing the API when the broker is unavailable.
            logger.exception(
                "Unable to queue AI diagnosis task for deployment %s",
                deployment.id,
            )

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
        MAX_UPLOAD_SIZE = getattr(settings, 'MAX_UPLOAD_SIZE', 100 * 1024 * 1024)
        if uploaded_file.size > MAX_UPLOAD_SIZE:
            size_mb = uploaded_file.size / 1024 / 1024
            return Response(
                {'error': f'File too large. Maximum size is 100MB, '
                          f'got {size_mb:.1f}MB'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        # Security: Validate file extension
        if not uploaded_file.name.endswith('.zip'):
            return Response(
                {'error': 'Invalid file type. Only .zip files are allowed'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # ZH-011 FIX: Verify ownership at query level (fail-closed)
            service = Service.objects.get(id=service_id, owner=request.user)

            # Security: Use secure upload directory
            import secrets
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
                commit_hash=f"upload-{uuid.uuid4().hex[:32]}",
                commit_message=f"CLI Upload: {uploaded_file.name}"
            )

            # If no provider set on service, find default
            provider = _resolve_provider_for_service(service)
            provider_id = str(provider.id) if provider else None
            if not provider_id:
                return Response(
                    {'error': 'No active cloud provider configured'},
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


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """ZH-001 FIX: Filter audit logs to only show entries for the requesting user."""
        if self.request.user.is_superuser:
            qs = AuditLog.objects.all()
        else:
            username = self.request.user.get_username()
            qs = AuditLog.objects.filter(actor=username)

        # Search filter
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(actor__icontains=search) |
                Q(target__icontains=search)
            )
        return qs


class SessionTokenView(APIView):
    """
    Exchange an authenticated Django session for a DRF token.
    Used by the frontend callback page to avoid token-in-URL leakage.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({'token': token.key})

class SystemConfigView(APIView):
    """
    Expose safe server configuration to the frontend.
    GET /api/v1/system/config/
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        return Response({
            # General
            'VERSION': '3.0.0',
            'DEBUG': settings.DEBUG,
            'DOMAIN': getattr(settings, 'DOMAIN', 'localhost'),
            'TIME_ZONE': settings.TIME_ZONE,
            'SITE_ID': settings.SITE_ID,

            # Security
            'USE_SSL': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_SSL_REDIRECT': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_HSTS_SECONDS': getattr(settings, 'SECURE_HSTS_SECONDS', 0),
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': getattr(settings, 'SECURE_HSTS_INCLUDE_SUBDOMAINS', False),
            'SECURE_HSTS_PRELOAD': getattr(settings, 'SECURE_HSTS_PRELOAD', False),
            'SESSION_COOKIE_SECURE': getattr(settings, 'SESSION_COOKIE_SECURE', False),
            'CSRF_COOKIE_SECURE': getattr(settings, 'CSRF_COOKIE_SECURE', False),
            'SMSLY_DISABLE_SIGNATURE_CHECK': getattr(settings, 'SMSLY_DISABLE_SIGNATURE_CHECK', False),

            # Network
            'ALLOWED_HOSTS': settings.ALLOWED_HOSTS,
            'CORS_ALLOWED_ORIGINS': getattr(settings, 'CORS_ALLOWED_ORIGINS', []),
            'CSRF_TRUSTED_ORIGINS': getattr(settings, 'CSRF_TRUSTED_ORIGINS', []),

            # Auth
            'ACCOUNT_AUTH_METHOD': getattr(settings, 'ACCOUNT_AUTHENTICATION_METHOD', 'username'),
            'LOGIN_REDIRECT_URL': getattr(settings, 'LOGIN_REDIRECT_URL', '/'),

            # Infrastructure — Redis / Celery
            'REDIS_HOST': getattr(settings, 'REDIS_HOST', 'redis'),
            'REDIS_PORT': getattr(settings, 'REDIS_PORT', '6379'),
            'REDIS_PASSWORD_SET': bool(getattr(settings, 'REDIS_PASSWORD', '')),
            'CELERY_RESULT_BACKEND': getattr(settings, 'CELERY_RESULT_BACKEND', ''),

            # Container Registry
            'CONTAINER_REGISTRY_URL': getattr(settings, 'CONTAINER_REGISTRY_URL', ''),
            'REGISTRY_USER': getattr(settings, 'REGISTRY_USER', '') or 'Not set',
            'REGISTRY_PASSWORD_SET': bool(getattr(settings, 'REGISTRY_PASSWORD', '')),

            # Rate Limiting
            'THROTTLE_RATES': settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {}),

            # Database (safe subset)
            'DATABASE_ENGINE': settings.DATABASES['default'].get('ENGINE', ''),
            'DATABASE_NAME': settings.DATABASES['default'].get('NAME', ''),
            'DATABASE_HOST': settings.DATABASES['default'].get('HOST', 'localhost'),

            # Webhook
            'GITHUB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')),
        })


class DomainConfigView(APIView):
    """
    Manage platform domain & SSL configuration.
    GET  /api/v1/system/domain-config/ → current config
    PUT  /api/v1/system/domain-config/ → update + apply Caddyfile
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        config = PlatformConfig.load()
        return Response({
            'domain': config.domain,
            'use_ssl': config.use_ssl,
            'wildcard_subdomains': config.wildcard_subdomains,
            'cloudflare_api_token_set': bool(config.cloudflare_api_token),
            'server_ip': config.server_ip or '',
            'caddy_status': config.caddy_status,
            'updated_at': config.updated_at,
        })

    def put(self, request):
        config = PlatformConfig.load()
        data = request.data

        # Update fields
        if 'domain' in data:
            config.domain = data['domain'].strip()
        if 'use_ssl' in data:
            config.use_ssl = bool(data['use_ssl'])
        if 'wildcard_subdomains' in data:
            config.wildcard_subdomains = bool(data['wildcard_subdomains'])
        if 'cloudflare_api_token' in data and data['cloudflare_api_token']:
            config.cloudflare_api_token = data['cloudflare_api_token'].strip()
        if 'server_ip' in data:
            config.server_ip = data['server_ip'].strip() or None

        # Validate: wildcard requires Cloudflare token
        if config.wildcard_subdomains and config.use_ssl and not config.cloudflare_api_token:
            return Response(
                {'error': 'Wildcard subdomains require a Cloudflare API Token.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        config.save()

        # Generate and apply Caddyfile
        try:
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
            caddyfile_content = generate_caddyfile(config)
            result = apply_caddyfile(caddyfile_content)
            config.caddy_status = 'applied' if result['ok'] else 'error'
            config.save(update_fields=['caddy_status'])
        except Exception as e:
            config.caddy_status = 'error'
            config.save(update_fields=['caddy_status'])
            return Response(
                {'error': f'Config saved but Caddyfile apply failed: {e}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'message': 'Domain configuration updated and Caddyfile applied.',
            'caddy_status': config.caddy_status,
            'caddyfile_preview': caddyfile_content,
        })
