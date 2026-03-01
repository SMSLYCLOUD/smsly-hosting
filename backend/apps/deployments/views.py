# pylint: disable=invalid-name
# pylint: disable=too-many-lines
"""Views module."""
from rest_framework import viewsets, permissions, status, parsers, serializers
from rest_framework.generics import GenericAPIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from apps.deployments.services.github_webhooks import setup_github_webhook
import threading
from apps.licensing.models import PlatformLicense
from apps.licensing.decorators import require_tier
from apps.deployments.services.github_webhooks import setup_github_webhook
import threading
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import (
    ServiceSerializer, DeploymentSerializer,
    DeploymentTriggerSerializer, EnvVarSerializer,
    DeploymentTimelineSerializer, InstantRollbackSerializer,
    AuditLogSerializer, DeploymentApproveSerializer,
    ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
)
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task
from .domain_utils import normalize_domain
from apps.cloud.models import CloudProvider
import os
import uuid
import logging
import re

logger = logging.getLogger(__name__)


class EmptySerializer(serializers.Serializer):
    """Schema placeholder for APIViews without request/response bodies."""


_IN_PROGRESS_DEPLOYMENT_STATUSES = [
    Deployment.Status.QUEUED,
    Deployment.Status.BUILDING,
    Deployment.Status.DEPLOYING,
    'REVIEW',  # Also block if awaiting review
]


def _cancel_stale_in_progress_deployments(service):
    """
    Cancel stale in-progress deployments superseded by a newer ACTIVE deploy.
    This prevents zombie QUEUED rows from permanently blocking new deploys.
    """
    latest_active = (
        service.deployments
        .filter(status=Deployment.Status.ACTIVE)
        .order_by('-created_at')
        .first()
    )
    if not latest_active:
        return 0

    stale_qs = service.deployments.filter(
        status__in=_IN_PROGRESS_DEPLOYMENT_STATUSES,
        created_at__lte=latest_active.created_at,
    )
    count = stale_qs.count()
    if count:
        stale_qs.update(status=Deployment.Status.CANCELLED, finished_at=timezone.now())
    return count


def _has_active_deployment(service):
    """
    Check if a service already has an active deployment in progress.
    Returns the existing deployment if found, None otherwise.
    Prevents rapid-fire deployment spam from the dashboard.
    """
    _cancel_stale_in_progress_deployments(service)
    return service.deployments.filter(
        status__in=_IN_PROGRESS_DEPLOYMENT_STATUSES
    ).order_by('-created_at').first()


def _resolve_provider_for_service(service: Service):
    """
    Resolve provider for deployment in a fail-closed way.
    - If service has an assigned provider, it must be active.
    - Otherwise, fall back to the first active global provider.
    """
    if service.provider:
        return service.provider if service.provider.is_active else None
    return CloudProvider.objects.filter(is_active=True).first()


def _normalize_request_domain(raw_domain: str):
    """Normalize and validate user-provided domains."""
    try:
        return normalize_domain(raw_domain), None
    except ValueError as exc:
        return None, str(exc)


def _resolve_domain_ips(hostname: str) -> set:
    """Resolve host to a set of IPs (A/AAAA). Returns empty set on errors."""
    import socket
    ips = set()
    try:
        for row in socket.getaddrinfo(hostname, 443):
            if row and len(row) >= 5 and row[4]:
                ips.add(row[4][0])
    except socket.gaierror:
        return set()
    return ips


def _cname_chain_contains(domain: str, target: str, max_hops: int = 10) -> bool:
    """
    Best-effort CNAME chain check.
    Returns False if dnspython is unavailable or lookup fails.
    """
    try:
        import dns.resolver  # type: ignore
    except Exception:  # pylint: disable=broad-exception-caught
        return False

    current = domain.rstrip(".").lower()
    wanted = target.rstrip(".").lower()
    seen = set()
    for _ in range(max_hops):
        if current in seen:
            return False
        seen.add(current)
        try:
            answers = dns.resolver.resolve(current, "CNAME")
        except Exception:  # pylint: disable=broad-exception-caught
            return False

        try:
            next_target = str(answers[0].target).rstrip(".").lower()
        except Exception:  # pylint: disable=broad-exception-caught
            return False

        if next_target == wanted:
            return True
        current = next_target

    return False


def _service_for_domain(domain: str):
    """Find service routed by this public/custom domain."""
    direct = Service.objects.filter(public_domain=domain).first()
    if direct:
        return direct

    for service in Service.objects.only("id", "custom_domains"):
        values = [
            str(value or "").strip().lower()
            for value in (service.custom_domains or [])
            if str(value or "").strip()
        ]
        if domain in values:
            return service
    return None


def _parse_bool(value):
    """Safely parse booleans from JSON or form-encoded payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


_ENV_KEY_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_MASKED_SECRET_PATTERN = re.compile(r'^[\*\u2022]{4,}$')


def _is_valid_env_key(key: str) -> bool:
    """Return True when an env var key is in shell-safe format."""
    return bool(_ENV_KEY_PATTERN.match(str(key or '').strip()))


def _looks_masked_secret(value: str) -> bool:
    """
    Detect masked secret placeholders from UI payloads.
    Accepts repeated asterisks or bullet characters.
    """
    return bool(_MASKED_SECRET_PATTERN.match(str(value or '').strip()))


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Service Management and Nested Resources.
    """
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """ZH-001 FIX: Only return services owned by the requesting user."""
        qs = self.queryset.prefetch_related('deployments')
        if self.request.user.is_superuser:
            return qs.all().order_by('-created_at')
        return qs.filter(owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        deploy_type = serializer.validated_data.get('deploy_type', 'GIT')
        tier_gates_disabled = bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False))
        if deploy_type == 'FUNCTION' and not tier_gates_disabled:
            license = PlatformLicense.load()
            if license.is_community:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Serverless Functions require Pro tier.")

        service = serializer.save(owner=self.request.user)

        # Setup GitHub Webhook if applicable
        if service.deploy_type == 'GIT' and service.repository_url:
            threading.Thread(
                target=setup_github_webhook,
                args=(self.request.user, service.repository_url),
                daemon=True
            ).start()

    def perform_destroy(self, instance):
        """Stop all deployments and audit-log before deleting the service."""
        # Cancel any active deployments
        instance.deployments.filter(
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
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
                Deployment.Status.REVIEW,
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
        Fast-restart a service by restarting its existing Docker container.
        POST /api/v1/services/{id}/restart/
        Body: { "force_rebuild": true } to trigger a full rebuild instead.

        Default behaviour (<5 seconds):
          1. Find the active deployment's container
          2. `docker restart` it
          3. Re-mark it ACTIVE

        With force_rebuild=true (full pipeline, minutes):
          1. Cancel active deployments
          2. Queue a fresh build + deploy
        """
        service = self.get_object()
        force_rebuild = _parse_bool(request.data.get('force_rebuild', False))

        # Clear health monitor restart state (ends exponential backoff)
        from apps.deployments.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))

        # ── Fast restart path: just docker restart the container ──
        if not force_rebuild:
            active_deploy = (
                service.deployments
                .filter(status=Deployment.Status.ACTIVE)
                .order_by('-created_at')
                .first()
            )
            container_id = active_deploy.container_id if active_deploy else None

            if container_id:
                try:
                    import docker as docker_lib
                    client = docker_lib.from_env()
                    container = client.containers.get(container_id)
                    container.restart(timeout=10)

                    # Update health status
                    service.health_status = 'starting'
                    service.save(update_fields=['health_status', 'updated_at'])

                    AuditLog(
                        actor=request.user.get_username(),
                        action='SERVICE_FAST_RESTART',
                        target=f'Service: {service.name}',
                        metadata={
                            'service_id': str(service.id),
                            'container_id': container_id,
                            'method': 'docker_restart',
                        },
                    ).save()

                    return Response({
                        'message': f'Service {service.name} restarted (fast)',
                        'method': 'docker_restart',
                        'container_id': container_id,
                    })
                except Exception as exc:
                    logger.warning(
                        "Fast restart failed for %s (container=%s): %s. "
                        "Falling back to full rebuild.",
                        service.name, container_id, exc,
                    )
                    # Fall through to full rebuild

        # ── Full rebuild path ──
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

        provider = _resolve_provider_for_service(service)
        if not provider:
            return Response({'error': 'No active cloud provider configured'},
                            status=status.HTTP_400_BAD_REQUEST)

        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash='latest',
            commit_message='Service restart (full rebuild)',
        )

        smart_deploy_task.delay(str(deployment.id), str(provider.id),
                               skip_review=True)

        AuditLog(
            actor=request.user.get_username(),
            action='SERVICE_RESTART',
            target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), 'deployment_id': str(deployment.id)},
        ).save()

        return Response({
            'message': f'Service {service.name} restarting (full rebuild)',
            'method': 'full_rebuild',
            'deployment': DeploymentSerializer(deployment).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='recheck-health')
    def recheck_health(self, request, pk=None):
        """
        Trigger an immediate health re-check for one service.
        Useful when a route was temporarily unavailable and needs unpark.
        """
        service = self.get_object()
        reset_backoff = _parse_bool(request.data.get('reset_backoff', True))

        try:
            from apps.deployments.services.health_monitor import (
                _check_service_health,  # intentional internal call for immediate check
                reset_restart_state,
            )

            if reset_backoff:
                reset_restart_state(str(service.id))

            _check_service_health(service, Deployment)
            service.refresh_from_db(fields=['health_status', 'updated_at'])

            latest = (
                service.deployments
                .order_by('-created_at')
                .values_list('status', flat=True)
                .first()
            )
            return Response({
                'service_id': str(service.id),
                'health_status': service.health_status,
                'latest_deployment_status': latest,
                'backoff_reset': reset_backoff,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Manual health recheck failed for %s: %s", service.id, exc)
            return Response(
                {'error': 'Failed to run health recheck'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['post'])
    def deploy(self, request, pk=None):
        """
        Manually trigger deployment for a service.
        POST /api/v1/services/{id}/deploy/
        Body: { "ref": "commit_hash" } (Optional)
        """
        service = self.get_object()
        ref = request.data.get('ref', 'HEAD')

        # Prevent rapid-fire deployment spam
        existing = _has_active_deployment(service)
        if existing:
            return Response({
                'error': f'Deployment already in progress (status: {existing.status}). '
                         'Wait for it to finish or cancel it first.',
                'existing_deployment': DeploymentSerializer(existing).data,
            }, status=status.HTTP_409_CONFLICT)

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

        try:
            smart_deploy_task.delay(str(deployment.id), str(provider.id))
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to enqueue deploy task for service=%s deployment=%s",
                service.id,
                deployment.id,
            )
            deployment.status = Deployment.Status.FAILED
            deployment.finished_at = timezone.now()
            deployment.build_logs = (
                (deployment.build_logs or '')
                + f"\n[ERROR] Failed to queue deployment task: {exc}\n"
            )
            deployment.save(
                update_fields=['status', 'finished_at', 'build_logs', 'updated_at']
            )
            return Response(
                {
                    'error': 'Failed to queue deployment task. Check Celery/Redis health.',
                    'deployment': DeploymentSerializer(deployment).data,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(DeploymentSerializer(deployment).data)

    # ── Preview Environments ─────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='create-preview')
    def create_preview(self, request, pk=None):
        """
        Create a preview environment from a specific branch/PR.
        POST /api/v1/services/{id}/create-preview/
        Body: { "branch": "feature/login", "pr_number": 42 }

        Clones the parent service config, sets is_preview=True, deploys
        on the specified branch with a unique subdomain.
        """
        parent = self.get_object()
        branch = request.data.get('branch')
        pr_number = request.data.get('pr_number')

        if not branch:
            return Response(
                {'error': 'branch is required'},
                status=status.HTTP_400_BAD_REQUEST)

        # Check for existing preview on same branch
        existing = Service.objects.filter(
            parent_service=parent, branch=branch, is_preview=True
        ).first()
        if existing:
            return Response({
                'error': f'Preview already exists for branch "{branch}"',
                'preview_id': str(existing.id),
                'preview_url': existing.service_url,
            }, status=status.HTTP_409_CONFLICT)

        # Build preview name: pr-42-myservice or preview-feature-login-myservice
        slug_branch = re.sub(r'[^a-z0-9]+', '-', branch.lower()).strip('-')[:30]
        if pr_number:
            preview_name = f"pr-{pr_number}-{parent.name}"[:255]
        else:
            preview_name = f"preview-{slug_branch}-{parent.name}"[:255]

        # Ensure unique name
        base_name = preview_name
        counter = 1
        while Service.objects.filter(name=preview_name).exists():
            preview_name = f"{base_name}-{counter}"[:255]
            counter += 1

        try:
            with transaction.atomic():
                preview = Service.objects.create(
                    name=preview_name,
                    repository_url=parent.repository_url,
                    branch=branch,
                    deploy_type=parent.deploy_type,
                    buildpack=parent.buildpack,
                    docker_image=parent.docker_image,
                    owner=parent.owner,
                    project=parent.project,
                    provider=parent.provider,
                    build_command=parent.build_command,
                    start_command=parent.start_command,
                    root_directory=parent.root_directory,
                    internal_port=parent.internal_port,
                    cpu_cores=parent.cpu_cores,
                    memory_mb=parent.memory_mb,
                    health_check_path=parent.health_check_path,
                    health_check_interval=parent.health_check_interval,
                    health_check_timeout=parent.health_check_timeout,
                    health_check_retries=parent.health_check_retries,
                    restart_policy=parent.restart_policy,
                    deploy_mode=parent.deploy_mode,
                    compose_file=parent.compose_file,
                    compose_main_service=parent.compose_main_service,
                    is_preview=True,
                    parent_service=parent,
                    pr_number=pr_number,
                )

                # Copy env vars from parent
                for env in parent.env_vars.all():
                    EnvironmentVariable.objects.create(
                        service=preview,
                        key=env.key,
                        value=env.value,
                        is_secret=env.is_secret,
                        source=env.source,
                    )

                # Create and trigger deployment
                deployment = Deployment.objects.create(
                    service=preview,
                    status=Deployment.Status.QUEUED,
                    commit_hash='HEAD',
                    commit_message=f"Preview deploy: {branch}"
                    + (f" (PR #{pr_number})" if pr_number else ""),
                )

                provider = _resolve_provider_for_service(preview)
                if provider:
                    smart_deploy_task.delay(
                        str(deployment.id), str(provider.id))

        except (IntegrityError, ValidationError) as exc:
            return Response(
                {'error': f'Failed to create preview: {str(exc)}'},
                status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'preview': ServiceSerializer(preview).data,
            'deployment': DeploymentSerializer(deployment).data,
            'preview_url': preview.service_url,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='previews')
    def list_previews(self, request, pk=None):
        """
        List all preview environments for a service.
        GET /api/v1/services/{id}/previews/
        """
        parent = self.get_object()
        previews = Service.objects.filter(
            parent_service=parent, is_preview=True
        ).order_by('-created_at')

        data = []
        for preview in previews:
            latest_deploy = preview.deployments.order_by('-created_at').first()
            data.append({
                'id': str(preview.id),
                'name': preview.name,
                'branch': preview.branch,
                'pr_number': preview.pr_number,
                'preview_url': preview.service_url,
                'health_status': preview.health_status,
                'created_at': preview.created_at.isoformat(),
                'latest_deployment': {
                    'id': str(latest_deploy.id),
                    'status': latest_deploy.status,
                    'created_at': latest_deploy.created_at.isoformat(),
                } if latest_deploy else None,
            })

        return Response({'count': len(data), 'results': data})

    @action(detail=True, methods=['delete'], url_path='destroy-preview')
    def destroy_preview(self, request, pk=None):
        """
        Destroy a preview environment.
        DELETE /api/v1/services/{id}/destroy-preview/
        Body: { "preview_id": "uuid" }
        """
        parent = self.get_object()
        preview_id = request.data.get('preview_id')

        if not preview_id:
            return Response(
                {'error': 'preview_id is required'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            preview = Service.objects.get(
                id=preview_id, parent_service=parent, is_preview=True)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Preview not found'},
                status=status.HTTP_404_NOT_FOUND)

        # Stop the container if running
        try:
            provider = _resolve_provider_for_service(preview)
            if provider:
                from apps.cloud.adapter import get_adapter
                adapter = get_adapter(provider)
                last_deploy = preview.deployments.filter(
                    status=Deployment.Status.ACTIVE
                ).first()
                if last_deploy and last_deploy.container_id:
                    adapter.stop_container(last_deploy.container_id)
        except Exception:
            logger.warning("Could not stop preview container for %s", preview_id)

        preview_name = preview.name
        preview.delete()

        return Response({
            'message': f'Preview "{preview_name}" destroyed',
        })

    @action(detail=True, methods=['post'], url_path='multi-deploy')
    def multi_deploy(self, request, pk=None):
        """
        Deploy a service to the local server AND selected remote servers.
        POST /api/v1/services/{id}/multi-deploy/
        Body: {
            "ref": "HEAD",
            "server_ids": ["uuid1", "uuid2"]
        }

        For each remote server:
        1. Check if a service with the same name exists
        2. If not, auto-create it (same repo_url, branch, buildpack, env vars)
        3. Trigger deploy on the remote server
        """
        import ipaddress
        from urllib.parse import urlparse
        import requests as req_lib
        from .models_servers import ManagedServer

        service = self.get_object()
        ref = str(request.data.get('ref', 'HEAD'))[:200]     # F7: bound length
        server_ids = request.data.get('server_ids', [])

        # F3: validate & cap server_ids
        if not isinstance(server_ids, list):
            return Response(
                {'error': 'server_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(server_ids) > 20:
            return Response(
                {'error': 'Maximum 20 remote servers per deploy'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {'local': None, 'remotes': []}

        # ── 1. Local deploy ─────────────────────────────────────
        existing = _has_active_deployment(service)
        if existing:
            results['local'] = {
                'status': 'skipped',
                'reason': f'Deployment already in progress ({existing.status})',
                'deployment': DeploymentSerializer(existing).data,
            }
        else:
            provider = _resolve_provider_for_service(service)
            if not provider:
                results['local'] = {
                    'status': 'error',
                    'reason': 'No active cloud provider configured',
                }
            else:
                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=ref if ref != 'HEAD' else 'latest',
                    commit_message=f"Multi-deploy: {ref}",
                )
                try:
                    smart_deploy_task.delay(str(deployment.id), str(provider.id))
                    results['local'] = {
                        'status': 'queued',
                        'deployment': DeploymentSerializer(deployment).data,
                    }
                except Exception as exc:
                    logger.exception('multi_deploy: local deploy task failed')
                    deployment.status = Deployment.Status.FAILED
                    deployment.finished_at = timezone.now()
                    deployment.build_logs = f"\n[ERROR] {exc}\n"
                    deployment.save(
                        update_fields=['status', 'finished_at', 'build_logs', 'updated_at'])
                    # F4: sanitize — don't leak raw exception to client
                    results['local'] = {
                        'status': 'error',
                        'reason': 'Failed to queue local deployment. Check server logs.',
                        'deployment': DeploymentSerializer(deployment).data,
                    }

        # ── 2. Remote deploys ───────────────────────────────────
        if server_ids:
            servers = ManagedServer.objects.filter(
                id__in=server_ids,
                owner=request.user,
            )
            # Build the service payload for auto-creation
            svc_payload = {
                'name': service.name,
                'deploy_type': service.deploy_type,
                'repository_url': service.repository_url,
                'docker_image': service.docker_image or '',
                'branch': service.branch or 'main',
                'buildpack': service.buildpack,
                'cpu_cores': float(service.cpu_cores),
                'memory_mb': service.memory_mb,
            }
            # Collect env vars (unmasked)
            env_vars = list(
                service.env_vars.all().values('key', 'value', 'is_secret')
            )

            for server in servers:
                remote_result = {
                    'server_id': str(server.id),
                    'server_name': server.name,
                }
                if not server.api_url:
                    remote_result['status'] = 'skipped'
                    remote_result['reason'] = 'No API URL (still provisioning?)'
                    results['remotes'].append(remote_result)
                    continue

                # F2: enforce HTTPS for secret transit
                if not server.api_url.startswith('https://'):
                    remote_result['status'] = 'error'
                    remote_result['reason'] = 'Remote server API URL must use HTTPS'
                    results['remotes'].append(remote_result)
                    continue

                # F1: SSRF protection — block private/reserved IPs
                parsed_url = urlparse(server.api_url)
                hostname = parsed_url.hostname or ''
                if hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
                    remote_result['status'] = 'error'
                    remote_result['reason'] = 'Loopback addresses are not allowed'
                    results['remotes'].append(remote_result)
                    continue
                try:
                    ip = ipaddress.ip_address(hostname)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        remote_result['status'] = 'error'
                        remote_result['reason'] = 'Private/reserved IP addresses are not allowed'
                        results['remotes'].append(remote_result)
                        continue
                except ValueError:
                    pass  # Hostname (not raw IP) — allow

                base_url = server.api_url.rstrip('/')
                headers = {
                    'Authorization': f'Bearer {server.api_token}',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                }

                try:
                    # Step A: Check if service exists on remote
                    svc_resp = req_lib.get(
                        f'{base_url}/api/v1/services/',
                        headers=headers,
                        timeout=15,
                    )
                    remote_services = svc_resp.json()
                    if isinstance(remote_services, dict):
                        remote_services = remote_services.get('results', [])

                    # Find matching service by name
                    matching = [
                        s for s in remote_services
                        if s.get('name') == service.name
                    ]
                    remote_svc_id = matching[0]['id'] if matching else None

                    # Step B: Auto-create if not found
                    if not remote_svc_id:
                        create_resp = req_lib.post(
                            f'{base_url}/api/v1/services/',
                            headers=headers,
                            json=svc_payload,
                            timeout=15,
                        )
                        if create_resp.status_code in (200, 201):
                            created_svc = create_resp.json()
                            remote_svc_id = created_svc.get('id')
                            remote_result['auto_created'] = True
                            logger.info(
                                'multi_deploy: auto-created service %s on server %s (%s)',
                                service.name, server.name, server.id,
                            )

                            # Set env vars on the new remote service
                            for ev in env_vars:
                                req_lib.post(
                                    f'{base_url}/api/v1/services/{remote_svc_id}/env_vars/',
                                    headers=headers,
                                    json=ev,
                                    timeout=10,
                                )
                        else:
                            # F5: don't leak remote error body to client
                            logger.error(
                                'multi_deploy: remote service creation failed on %s — HTTP %s: %s',
                                server.name, create_resp.status_code, create_resp.text[:500],
                            )
                            remote_result['status'] = 'error'
                            remote_result['reason'] = (
                                f'Remote server returned HTTP {create_resp.status_code}'
                            )
                            results['remotes'].append(remote_result)
                            continue

                    # Step C: Trigger deploy on remote
                    deploy_resp = req_lib.post(
                        f'{base_url}/api/v1/services/{remote_svc_id}/deploy/',
                        headers=headers,
                        json={'ref': ref},
                        timeout=15,
                    )
                    if deploy_resp.status_code in (200, 201):
                        remote_result['status'] = 'queued'
                        remote_result['deployment'] = deploy_resp.json()
                    else:
                        # F5: don't leak remote error body
                        logger.error(
                            'multi_deploy: remote deploy failed on %s — HTTP %s: %s',
                            server.name, deploy_resp.status_code, deploy_resp.text[:500],
                        )
                        remote_result['status'] = 'error'
                        remote_result['reason'] = (
                            f'Remote deploy returned HTTP {deploy_resp.status_code}'
                        )

                except req_lib.RequestException as e:
                    logger.error('multi_deploy: connection to %s failed: %s', server.name, e)
                    remote_result['status'] = 'error'
                    remote_result['reason'] = 'Connection to remote server failed'

                results['remotes'].append(remote_result)

        return Response(results, status=status.HTTP_202_ACCEPTED)

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

        payload_vars = request.data.get('vars')
        if payload_vars is not None:
            if not isinstance(payload_vars, list):
                return Response(
                    {'error': '"vars" must be a list of objects.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized = []
            seen_keys = set()

            for idx, row in enumerate(payload_vars):
                if not isinstance(row, dict):
                    return Response(
                        {'error': f'Invalid item at index {idx}; expected object.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                key = str(row.get('key') or '').strip()
                if not key:
                    return Response(
                        {'error': f'Missing key at index {idx}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not _is_valid_env_key(key):
                    return Response(
                        {'error': f'Invalid environment variable key "{key}".'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if key in seen_keys:
                    return Response(
                        {'error': f'Duplicate key "{key}" in import payload.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen_keys.add(key)

                existing = EnvironmentVariable.objects.filter(
                    service=service, key=key).first()
                value = str(row.get('value', '') or '')
                if existing and existing.is_secret and _looks_masked_secret(value):
                    value = existing.value

                if 'is_secret' in row:
                    is_secret = _parse_bool(row.get('is_secret'))
                else:
                    is_secret = bool(existing.is_secret) if existing else False

                normalized.append({
                    'key': key,
                    'value': value,
                    'is_secret': is_secret,
                })

            added = 0
            updated = 0
            try:
                with transaction.atomic():
                    for item in normalized:
                        _, created = EnvironmentVariable.objects.update_or_create(
                            service=service,
                            key=item['key'],
                            defaults={
                                'value': item['value'],
                                'is_secret': item['is_secret'],
                                'source': 'USER',
                            },
                        )
                        if created:
                            added += 1
                        else:
                            updated += 1
            except (ValidationError, DataError, IntegrityError) as exc:
                logger.warning(
                    "Invalid bulk env payload for service %s: %s",
                    service.id, exc,
                )
                return Response(
                    {'error': 'Invalid environment variable payload.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Failed bulk env upsert for service %s: %s", service.id, exc)
                return Response(
                    {'error': 'Failed to save environment variables'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            serializer = EnvVarSerializer(
                service.env_vars.all().order_by('key'), many=True)
            return Response({
                'added': added,
                'updated': updated,
                'count': len(normalized),
                'env_vars': serializer.data,
            })

        # Allow partial data — key is required, value can be empty
        key = str(request.data.get('key') or '').strip()
        if not key:
            return Response(
                {'key': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_env_key(key):
            return Response(
                {'key': ['Use letters, numbers, and underscore; cannot start with a number.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = EnvironmentVariable.objects.filter(service=service, key=key).first()
        value = str(request.data.get('value', '') or '')
        if existing and existing.is_secret and _looks_masked_secret(value):
            value = existing.value
        if 'is_secret' in request.data:
            is_secret = _parse_bool(request.data.get('is_secret'))
        else:
            is_secret = bool(existing.is_secret) if existing else False

        try:
            env_var, created = EnvironmentVariable.objects.update_or_create(
                service=service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret, 'source': 'USER'},
            )
        except (ValidationError, DataError, IntegrityError) as exc:
            logger.warning("Invalid env var payload for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': f'Invalid environment variable payload for key "{key}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to save env var for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': 'Failed to save environment variable'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    @require_tier('pro', 'enterprise')
    def verify_domain(self, request, pk=None):
        """
        Verify that a custom domain's DNS points to this service's server.
        POST /api/v1/services/{id}/verify-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Compare against the service's own public domain which already
        # resolves to the correct server IP. No hardcoded CNAME needed.
        cname_target = service.public_domain or ''
        if not cname_target:
            return Response({
                'domain': domain,
                'verified': False,
                'cname_target': '',
                'message': 'Service has no public domain assigned yet.',
            })

        domain_ips = _resolve_domain_ips(domain)
        expected_ips = _resolve_domain_ips(cname_target)
        is_valid = bool(domain_ips and expected_ips and (domain_ips & expected_ips))

        # Apex domains can point directly to server IP instead of CNAME.
        server_ip = ""
        try:
            server_ip = str(PlatformConfig.load().server_ip or "").strip()
        except Exception:  # pylint: disable=broad-exception-caught
            server_ip = ""
        if not is_valid and server_ip and server_ip in domain_ips:
            is_valid = True

        # Extra robustness: follow CNAME chain when dnspython is available.
        if not is_valid and _cname_chain_contains(domain, cname_target):
            is_valid = True

        # ── Persist the verification result on the Service model ──
        # This is the critical step that was missing. Without this, the
        # domain_verified field stays False forever and the frontend badge
        # never updates.
        if is_valid and not service.domain_verified:
            service.domain_verified = True
            service.save(update_fields=['domain_verified'])
        elif not is_valid and service.domain_verified:
            service.domain_verified = False
            service.save(update_fields=['domain_verified'])

        return Response({
            'domain': domain,
            'verified': is_valid,
            'cname_target': cname_target,
            'message': (
                'DNS verified! Domain points to CloudNeuron.'
                if is_valid
                else (
                    f'DNS not configured. Add a CNAME record pointing to {cname_target} '
                    f'or an A record to {server_ip or "your server IP"}.'
                )
            ),
        })

    def _sync_caddy(self):
        """Regenerate Caddyfile with all custom domains and trigger reload."""
        try:
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
            from .models import PlatformConfig
            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result['ok']:
                logger.info("Caddy synced after domain change")
            else:
                logger.error("Caddy sync failed: %s", result['message'])
            return result['ok']
        except Exception as e:
            logger.error("Caddy sync error: %s", e)
            return False

    def _find_domain_conflict(self, service: Service, domain: str):
        """Return conflicting service if domain is already assigned globally."""
        public_conflict = (
            Service.objects
            .exclude(id=service.id)
            .filter(public_domain=domain)
            .only("id", "name", "public_domain")
            .first()
        )
        if public_conflict:
            return public_conflict

        for other in Service.objects.exclude(id=service.id).only("id", "name", "custom_domains"):
            other_domains = [
                str(d or "").strip().lower()
                for d in (other.custom_domains or [])
                if str(d or "").strip()
            ]
            if domain in other_domains:
                return other
        return None

    def _enforce_custom_domain_quota(self, service: Service, new_total: int):
        """
        Enforce billing plan limit for custom domains.
        Defaults to 1 when no active subscription exists.
        """
        try:
            from apps.billing.models import UserSubscription
            sub = (
                UserSubscription.objects
                .select_related("plan")
                .filter(
                    user=service.owner,
                    status__in=["ACTIVE", "TRIAL"],
                )
                .first()
            )
            limit = 1
            if sub and sub.plan and sub.plan.max_custom_domains is not None:
                limit = int(sub.plan.max_custom_domains)
        except Exception:  # pylint: disable=broad-exception-caught
            limit = 1

        # Non-positive means unlimited.
        if limit > 0 and new_total > limit:
            return Response(
                {
                    "error": (
                        f"Custom domain limit reached ({limit}). "
                        "Upgrade your plan to add more domains."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @action(detail=True, methods=['post'], url_path='add-domain')
    @require_tier('pro', 'enterprise')
    def add_domain(self, request, pk=None):
        """
        Add a custom domain to the service.
        POST /api/v1/services/{id}/add-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain in domains:
            return Response({'error': 'Domain already added'},
                            status=status.HTTP_400_BAD_REQUEST)

        conflict = self._find_domain_conflict(service, domain)
        if conflict:
            return Response(
                {
                    'error': (
                        f'Domain already assigned to service "{conflict.name}". '
                        'A domain can only be attached to one service.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        domains = list(dict.fromkeys([*domains, domain]))
        quota_response = self._enforce_custom_domain_quota(service, len(domains))
        if quota_response is not None:
            return quota_response

        previous_domains = list(service.custom_domains or [])
        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        # Auto-sync Caddyfile so SSL + routing are provisioned immediately.
        # No service redeploy is required.
        caddy_ok = self._sync_caddy()
        if not caddy_ok:
            service.custom_domains = previous_domains
            service.save(update_fields=['custom_domains'])
            return Response(
                {
                    'error': (
                        'Domain saved failed to apply routing configuration. '
                        'Change has been rolled back. Please retry.'
                    ),
                    'caddy_synced': False,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'domain': domain,
            'domains': domains,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'message': f'{domain} added. Configure DNS to point to your server. No redeploy required.',
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='delete-domain')
    @require_tier('pro', 'enterprise')
    def delete_domain(self, request, pk=None):
        """
        Remove a custom domain from the service.
        POST /api/v1/services/{id}/delete-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain not in domains:
            return Response({'error': 'Domain not found'},
                            status=status.HTTP_404_NOT_FOUND)

        previous_domains = list(service.custom_domains or [])
        domains = [d for d in domains if d != domain]
        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        # Auto-sync Caddyfile so stale domain entry is removed immediately.
        caddy_ok = self._sync_caddy()
        if not caddy_ok:
            service.custom_domains = previous_domains
            service.save(update_fields=['custom_domains'])
            return Response(
                {
                    'error': (
                        'Domain removal failed to apply routing configuration. '
                        'Change has been rolled back. Please retry.'
                    ),
                    'caddy_synced': False,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'domains': domains,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'message': f'{domain} removed. No redeploy required.',
        })


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

    def get_queryset(self):
        """ZH-002 FIX: Only return deployments for services owned by the requesting user."""
        if self.request.user.is_superuser:
            return self.queryset.all()
        return self.queryset.filter(service__owner=self.request.user)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """
        Rollback to this specific deployment.
        Effectively triggers a new deployment using the commit hash/image
        from this one.
        """
        target_deployment = self.get_object()
        service = target_deployment.service

        # Validate the target deployment
        if not target_deployment.commit_hash:
            return Response(
                {'error': 'Cannot rollback: target deployment has no commit hash.'},
                status=status.HTTP_400_BAD_REQUEST)

        if target_deployment.status not in ('ACTIVE', 'SUCCEEDED'):
            return Response(
                {'error': f'Cannot rollback to a {target_deployment.status} deployment. '
                          'Only successful deployments can be rolled back to.'},
                status=status.HTTP_400_BAD_REQUEST)

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

                # Prevent rapid-fire deployment spam
                existing = _has_active_deployment(service)
                if existing:
                    return Response({
                        'error': f'Deployment already in progress (status: {existing.status}). '
                                 'Wait for it to finish or cancel it first.',
                        'existing_deployment': DeploymentSerializer(existing).data,
                    }, status=status.HTTP_409_CONFLICT)

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
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
        ):
            return Response(
                {'error': f'Cannot cancel deployment in {deployment.status} '
                          f'status. Only QUEUED, REVIEW, or BUILDING '
                          f'deployments can be cancelled.'},
                status=status.HTTP_409_CONFLICT)

        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs += "\n\n[CANCELLED] Deployment cancelled by user."
        deployment.save()

        # Clean up orphaned build dir from analysis phase (REVIEW status only)
        if deployment.status == Deployment.Status.CANCELLED:
            import glob
            import shutil
            import tempfile
            tmp_pattern = os.path.join(
                tempfile.gettempdir(),
                f"build_{deployment.id}_*"
            )
            for d in glob.glob(tmp_pattern):
                shutil.rmtree(d, ignore_errors=True)

        return Response(DeploymentSerializer(deployment).data)

    @action(detail=False, methods=['post'], url_path='bulk-cancel')
    def bulk_cancel(self, request):
        """
        Cancel multiple deployments at once.
        POST /api/v1/deployments/bulk-cancel/
        Body: { "deployment_ids": ["uuid1", "uuid2", ...] }
        """
        deployment_ids = request.data.get('deployment_ids', [])
        if not deployment_ids or not isinstance(deployment_ids, list):
            return Response(
                {'error': 'deployment_ids must be a non-empty list'},
                status=status.HTTP_400_BAD_REQUEST)

        # Only allow cancelling deployments the user owns
        qs = self.get_queryset().filter(
            id__in=deployment_ids,
            status__in=[
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
                Deployment.Status.BUILDING,
                Deployment.Status.FAILED,
            ]
        )
        count = qs.update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        return Response({
            'cancelled': count,
            'message': f'{count} deployment(s) cancelled.',
        })

    @action(detail=True, methods=['get'])
    def review(self, request, pk=None):
        """
        Get pre-deploy review summary.
        GET /api/v1/deployments/{id}/review/
        Returns AI-recommended resources, env vars, issues, and addons.
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting review.'},
                status=status.HTTP_409_CONFLICT)

        return Response({
            'id': str(deployment.id),
            'service': str(deployment.service_id),
            'service_name': deployment.service.name,
            'status': deployment.status,
            'review_summary': deployment.review_summary,
            'build_logs': deployment.build_logs,
            'created_at': deployment.created_at.isoformat(),
        })

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Approve a deployment review and continue to build.
        POST /api/v1/deployments/{id}/approve/
        Body (all optional):
          { "cpu_cores": 1.0, "memory_mb": 1024,
            "env_overrides": {"KEY": "value"} }
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting approval.'},
                status=status.HTTP_409_CONFLICT)

        serializer = DeploymentApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        service = deployment.service
        updated_fields = []

        # Apply resource overrides
        cpu = data.get('cpu_cores')
        if cpu is not None:
            service.cpu_cores = cpu
            updated_fields.append('cpu_cores')

        mem = data.get('memory_mb')
        if mem is not None:
            service.memory_mb = mem
            updated_fields.append('memory_mb')

        if updated_fields:
            service.save(update_fields=updated_fields)

        # Apply env var overrides
        env_overrides = data.get('env_overrides', {})
        for key, value in env_overrides.items():
            key = key.strip().upper()
            if not key:
                continue
            EnvironmentVariable.objects.update_or_create(
                service=service, key=key,
                defaults={'value': value}
            )

        # Resolve provider BEFORE changing status (fail-safe: stays in
        # REVIEW if no provider, so user can retry)
        provider = _resolve_provider_for_service(service)
        if not provider:
            return Response(
                {'error': 'No active cloud provider configured'},
                status=status.HTTP_400_BAD_REQUEST)

        # Provider exists — now safe to transition status
        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save(update_fields=['status', 'started_at'])

        resume_deploy_task.delay(
            str(deployment.id), str(provider.id)
        )

        return Response({
            'message': 'Deployment approved — build starting',
            'deployment': DeploymentSerializer(deployment).data,
        })

    @action(detail=True, methods=['post'])
    def promote(self, request, pk=None):
        """
        Manually promote a STAGED deployment to ACTIVE (immediate swap).
        POST /api/v1/deployments/{id}/promote/

        Skips the bake timer and performs immediate blue-green cutover.
        Used for development/testing when you want instant promotion.
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.STAGED:
            return Response(
                {'error': f'Cannot promote: deployment is {deployment.status}, '
                          'not STAGED.'},
                status=status.HTTP_409_CONFLICT)

        provider = _resolve_provider_for_service(deployment.service)
        if not provider:
            return Response(
                {'error': 'No active cloud provider configured'},
                status=status.HTTP_400_BAD_REQUEST)

        from apps.deployments.tasks import promote_deployment_task
        promote_deployment_task.delay(
            str(deployment.id), str(provider.id)
        )

        return Response({
            'message': 'Promotion triggered — routing will swap momentarily',
            'deployment': DeploymentSerializer(deployment).data,
        })

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
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()

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
        except Exception as exc:
            # Avoid hard-failing the API when the broker is unavailable.
            try:
                from kombu.exceptions import OperationalError as BrokerOperationalError
            except Exception:  # pragma: no cover
                BrokerOperationalError = tuple()

            if BrokerOperationalError and isinstance(exc, BrokerOperationalError):
                logger.warning(
                    "Unable to queue AI diagnosis task for deployment %s: broker unavailable",
                    deployment.id,
                )
            else:
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
        if not uploaded_file.name.lower().endswith('.zip'):
            return Response(
                {'error': 'Invalid file type. Only .zip files are allowed'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # ZH-011 FIX: Verify ownership at query level (fail-closed)
            service = Service.objects.get(id=service_id, owner=request.user)

            # Security: Use secure upload directory
            import secrets
            base_dir = getattr(settings, 'MEDIA_ROOT', '/app/media')
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
            from pathlib import Path
            service.deploy_type = 'UPLOAD'
            service.repository_url = Path(file_path).resolve().as_uri()
            service.save(update_fields=['deploy_type', 'repository_url', 'updated_at'])

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

    @require_tier('enterprise')
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @require_tier('enterprise')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

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


class SessionTokenView(GenericAPIView):
    """
    Exchange an authenticated Django session for a DRF token.
    Used by the frontend callback page to avoid token-in-URL leakage.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({'token': token.key})

class SystemConfigView(GenericAPIView):
    """
    Expose safe server configuration to the frontend.
    GET /api/v1/system/config/
    """
    serializer_class = EmptySerializer
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

            # Database (H-2 fix: expose only safe boolean flags, not internals)
            'DATABASE_CONFIGURED': bool(settings.DATABASES['default'].get('HOST')),
            'DATABASE_ENGINE_TYPE': 'postgres' if 'postgresql' in settings.DATABASES['default'].get('ENGINE', '') else 'other',

            # Webhook
            'GITHUB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')),
        })


class DomainConfigView(GenericAPIView):
    """
    Manage platform domain & SSL configuration.
    GET  /api/v1/system/domain-config/ → current config
    PUT  /api/v1/system/domain-config/ → update + apply Caddyfile
    """
    serializer_class = EmptySerializer
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
            raw_domain = str(data.get('domain') or '').strip()
            if raw_domain:
                domain, domain_error = _normalize_request_domain(raw_domain)
                if domain_error:
                    return Response(
                        {'error': f'Invalid domain: {domain_error}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                config.domain = domain
            else:
                config.domain = ''
        if 'use_ssl' in data:
            config.use_ssl = _parse_bool(data.get('use_ssl'))
        if 'wildcard_subdomains' in data:
            config.wildcard_subdomains = _parse_bool(data.get('wildcard_subdomains'))
        if 'cloudflare_api_token' in data:
            # Allow explicit clear by sending an empty string.
            config.cloudflare_api_token = str(
                data.get('cloudflare_api_token') or ''
            ).strip()
        if 'server_ip' in data:
            config.server_ip = str(data.get('server_ip') or '').strip() or None

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
            cf_token = (config.cloudflare_api_token or "").strip()
            result = apply_caddyfile(caddyfile_content, cloudflare_token=cf_token)
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
            'cloudflare_api_token_set': bool(config.cloudflare_api_token),
            'caddyfile_preview': caddyfile_content,
        })


class RouteRecheckView(GenericAPIView):
    """
    Public route recheck hook for fallback pages.

    Allows a domain-level health recheck without requiring a dashboard login.
    This is intentionally rate-limited and only operates on known service domains.
    """

    serializer_class = EmptySerializer
    permission_classes = [permissions.AllowAny]

    @staticmethod
    def _cors(response: Response):
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    def options(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        return self._cors(Response(status=status.HTTP_204_NO_CONTENT))

    def _extract_domain(self, request):
        raw_host = (
            request.query_params.get("host")
            or request.data.get("host")
            or request.get_host()
        )
        host = str(raw_host or "").strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        domain, domain_error = _normalize_request_domain(host)
        if domain_error:
            return None, domain_error
        return domain, None

    def _trigger_recheck(self, service):
        try:
            from apps.deployments.services.health_monitor import (
                _check_service_health,
                reset_restart_state,
            )

            reset_restart_state(str(service.id))
            _check_service_health(service, Deployment)
            service.refresh_from_db(fields=["health_status"])
            return True, service.health_status
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Route recheck failed for service %s: %s", service.id, exc)
            return False, "unknown"

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    def _handle(self, request):
        domain, domain_error = self._extract_domain(request)
        if domain_error:
            return self._cors(
                Response(
                    {"error": f"Invalid domain: {domain_error}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            )

        service = _service_for_domain(domain)
        if not service:
            return self._cors(
                Response(
                    {"error": "Domain is not mapped to a service"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            )

        client_ip = (
            str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")[0].strip()
            or str(request.META.get("REMOTE_ADDR", "unknown")).strip()
            or "unknown"
        )
        throttle_key = f"route-recheck:{service.id}:{client_ip}"
        if cache.get(throttle_key):
            return self._cors(
                Response(
                    {"error": "Recheck already requested. Try again in a few seconds."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            )
        cache.set(throttle_key, True, timeout=20)

        ok, health_status = self._trigger_recheck(service)
        if not ok:
            return self._cors(
                Response(
                    {"error": "Failed to run health recheck"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            )

        return self._cors(
            Response(
                {
                    "message": "Health recheck triggered",
                    "service_id": str(service.id),
                    "health_status": health_status,
                }
            )
        )

class ServiceBackupViewSet(viewsets.ModelViewSet):
    queryset = ServiceBackup.objects.all()
    serializer_class = ServiceBackupSerializer
    permission_classes = [permissions.IsAuthenticated]

    @require_tier('pro', 'enterprise')
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        return self.queryset.filter(service__owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        backup = serializer.save(created_by=self.request.user, status='PENDING')
        create_service_backup_task.delay(str(backup.service.id), 'MANUAL')

    @action(detail=True, methods=['post'])
    @require_tier('pro', 'enterprise')
    def restore(self, request, pk=None):
        backup = self.get_object()
        target_service_id = request.data.get('target_service_id')

        if target_service_id:
            if not Service.objects.filter(
                id=target_service_id,
                owner=request.user,
            ).exists():
                return Response(
                    {'error': 'Target service not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        restore_service_backup_task.delay(
            str(backup.id),
            str(target_service_id) if target_service_id else None,
            request.user.id,
        )
        return Response({'status': 'restore_started'})

    @action(detail=True, methods=['get'])
    @require_tier('pro', 'enterprise')
    def download(self, request, pk=None):
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)
        from django.http import FileResponse
        return FileResponse(open(backup.file_path, 'rb'), as_attachment=True)

class ServerBackupViewSet(viewsets.ModelViewSet):
    serializer_class = ServerBackupSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = ServerBackup.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(status='PENDING')
        create_server_backup_task.delay()

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        return Response({'error': 'Server restore via API not implemented. Use CLI.'}, status=status.HTTP_501_NOT_IMPLEMENTED)

class BackupScheduleViewSet(viewsets.ModelViewSet):
    queryset = BackupSchedule.objects.all()
    serializer_class = BackupScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    @require_tier('pro', 'enterprise')
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        return self.queryset.filter(service__owner=self.request.user)
from .views_transfer import ServerTransferViewSet
