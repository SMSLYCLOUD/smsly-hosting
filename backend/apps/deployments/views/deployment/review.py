"""review mixin."""
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)


from django.utils import timezone
from ...models import Deployment
from ...serializers import DeploymentApproveSerializer, DeploymentSerializer
from ...tasks import resume_deploy_task
from .._helpers import _resolve_provider_for_target


class ReviewActionsMixin:
    """ReviewActions actions for the viewset."""


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


    @action(detail=True, methods=['post'], url_path='fill-external-env')
    def fill_external_env(self, request, pk=None):
        """
        Auto-fills unresolved external environment variables with safe placeholders.
        POST /api/v1/deployments/{id}/fill-external-env/
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting review.'},
                status=status.HTTP_409_CONFLICT)

        summary = deployment.review_summary or {}
        unresolved_vars = summary.get('unresolved_external_vars', [])

        if not unresolved_vars:
            return Response({'message': 'No unresolved external variables found.'})

        from apps.deployments.models import EnvironmentVariable
        from apps.deployments.services.manifest_env_resolver import ManifestEnvResolver

        injected = 0
        for var_name in unresolved_vars:
            key_upper = var_name.strip().upper()
            if not key_upper:
                continue

            # Check if user already set it
            if EnvironmentVariable.objects.filter(service=deployment.service, key=key_upper).exists():
                continue

            placeholder = ManifestEnvResolver.generate_placeholder_for_external(var_name)

            from apps.cloud.services.build_constants import is_secret_env_var
            is_secret = is_secret_env_var(key_upper)

            EnvironmentVariable.objects.create(
                service=deployment.service,
                key=key_upper,
                value=placeholder,
                is_secret=is_secret,
            )
            injected += 1

        # Clear the unresolved vars from the summary so it doesn't prompt again
        if 'unresolved_external_vars' in summary:
            del summary['unresolved_external_vars']
            deployment.review_summary = summary
            deployment.build_logs += f"\n✅ Auto-filled {injected} external variables with placeholders.\n"
            deployment.save(update_fields=['review_summary', 'build_logs'])

        return Response({
            'message': f'Auto-filled {injected} variables with placeholders.',
            'injected_count': injected,
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
            from ...models import EnvironmentVariable
            EnvironmentVariable.objects.update_or_create(
                service=service, key=key,
                defaults={'value': value}
            )

        # Resolve provider BEFORE changing status (fail-safe: stays in
        # REVIEW if no provider, so user can retry)
        provider = _resolve_provider_for_target(
            service,
            target_is_local=bool(getattr(deployment, 'target_is_local', False)),
        )
        if not provider:
            message = (
                'No active local cloud provider configured'
                if getattr(deployment, 'target_is_local', False)
                else 'No active cloud provider configured'
            )
            return Response(
                {'error': message},
                status=status.HTTP_400_BAD_REQUEST)

        # Provider exists — now safe to transition status
        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save(update_fields=['status', 'started_at'])

        resume_deploy_task.delay(
            deployment_id=str(deployment.id), provider_id=str(provider.id)
        )

        return Response({
            'message': 'Deployment approved — build starting',
            'deployment': DeploymentSerializer(deployment).data,
        })
