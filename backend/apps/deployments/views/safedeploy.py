import logging

from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Q
from rest_framework import authentication, permissions, status, viewsets
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.core.auth import CsrfExemptSessionAuthentication
from apps.deployments.models.audit import AuditLog
from apps.deployments.models.core import Deployment, Service
from apps.deployments.models.safedeploy import DeploymentApproval, PreviewEnvironment
from apps.deployments.permissions import CanApproveDeployment, CanManagePreviews
from apps.deployments.serializers import (
    ApprovalRejectSerializer,
    DeploymentApprovalSerializer,
    PreviewCreateSerializer,
    PreviewEnvironmentSerializer,
    PreviewRebuildSerializer,
)
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BranchPreviewManager,
)
from apps.teams.permissions import assert_can_write, get_team_q_filter

logger = logging.getLogger(__name__)


class PreviewThrottle(UserRateThrottle):
    rate = '30/minute'


class ApprovalThrottle(UserRateThrottle):
    rate = '20/minute'


MAX_PREVIEWS_PER_CREATOR = getattr(django_settings, 'MAX_PREVIEWS_PER_CREATOR', 10)


def send_approval_notification(approval, service_pk):
    """Send an approval-state-change notification to the requester.

    Tries the platform notification dispatcher first; falls back to
    Django's ``send_mail`` if the user has an email address and the
    email backend is configured.  All failures are logged and
    swallowed so the caller's approve/reject flow is never blocked by
    a misconfigured email backend.
    """
    requester = getattr(approval, 'requested_by', None)
    requester_email = getattr(requester, 'email', None)
    status_label = (approval.status or '').lower()
    deployment_id = getattr(getattr(approval, 'deployment', None), 'id', None)

    if not requester_email:
        logger.info(
            "send_approval_notification: requester=%s has no email; "
            "skipping notification for approval=%s status=%s",
            getattr(requester, 'id', None),
            getattr(approval, 'id', None),
            status_label,
        )
        return

    subject = f"[Grid] Deployment {status_label.upper()}"
    message = (
        f"Your deployment (id={deployment_id}) was {status_label}.\n"
        f"Service: {service_pk}\n"
        f"Approval: {getattr(approval, 'id', None)}\n"
    )

    delivered = False
    try:
        from apps.notifications.tasks import dispatch_notification
        dispatch_notification.delay(
            event_type='deployment_approval',
            user_id=requester.id,
            title=subject,
            message=message,
            metadata={
                'service_pk': str(service_pk) if service_pk else None,
                'deployment_id': str(deployment_id) if deployment_id else None,
                'approval_id': str(getattr(approval, 'id', None)),
                'approval_status': status_label,
            },
            channels=['email'],
        )
        delivered = True
    except Exception as exc:
        logger.warning(
            "send_approval_notification: dispatch_notification failed (%s); "
            "falling back to send_mail",
            exc,
        )

    if not delivered:
        try:
            from django.core.mail import send_mail
            send_mail(
                subject=subject,
                message=message,
                from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.cloud'),
                recipient_list=[requester_email],
                fail_silently=True,
            )
        except Exception as exc:
            logger.warning(
                "send_approval_notification: send_mail fallback failed for %s: %s",
                requester_email, exc,
            )


class PreviewEnvironmentViewSet(viewsets.ModelViewSet):
    queryset = PreviewEnvironment.objects.all()
    serializer_class = PreviewEnvironmentSerializer
    # Use CsrfExemptSessionAuthentication for the session fallback: these
    # endpoints are called from the same-origin frontend which authenticates
    # via the HttpOnly auth cookie. The cookie is already a strong
    # same-origin credential (SameSite=Lax/Strict) and the endpoints
    # require an explicit permission check (CanManagePreviews) on top of
    # authentication, so CSRF adds friction without meaningful protection.
    # Token-based callers (CLI, automations) still get CSRF enforcement
    # via the default TokenAuthentication path.
    authentication_classes = [
        authentication.TokenAuthentication,
        CsrfExemptSessionAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated, CanManagePreviews]

    def _user_owns_or_member(self, service):
        user = self.request.user
        if not user or not user.is_authenticated:
            # Mirror views.py:756-759 — guard against AnonymousUser being
            # passed into FK lookups. Django's Q(owner=user) would raise
            # TypeError: Cannot cast AnonymousUser to int.
            return False
        if service.owner == user:
            return True
        team = getattr(service.project, 'team', None)
        return bool(team and team.members.filter(user=user).exists())

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            # Same defensive pattern as ServiceViewSet.get_queryset
            # (views.py:756-759). The viewset default IsAuthenticated makes
            # this unreachable in normal flow, but guard against future
            # regressions where a public @action is added on this viewset
            # (cf. ServiceBackupViewSet.download_key which previously
            # crashed with AnonymousUser cast).
            return self.queryset.none()
        service_id = self.kwargs.get('service_pk')
        allowed_services = Service.objects.filter(get_team_q_filter(user))
        qs = self.queryset.filter(
            Q(service__in=allowed_services)
        ).distinct()
        if service_id:
            return qs.filter(service_id=service_id)
        return qs

    def _get_service(self, service_pk):
        try:
            service = Service.objects.get(id=service_pk)
        except Service.DoesNotExist:
            return None
        if not self._user_owns_or_member(service):
            return None
        return service

    def _check_service_feature_flags(self, service):
        if not service.preview_environments_enabled:
            import logging
            logging.getLogger(__name__).warning("Preview environment creation rejected: preview_environments_enabled is False for service %s", service.id)
            return Response(
                {"error": "Preview environments are not enabled for this service"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @throttle_classes([PreviewThrottle])
    def create(self, request, *args, **kwargs):
        service_id = kwargs.get('service_pk')
        service = self._get_service(service_id)
        if service is None:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)
        assert_can_write(self.request.user, service, action='create preview')

        flag_error = self._check_service_feature_flags(service)
        if flag_error:
            return flag_error

        existing = PreviewEnvironment.objects.filter(
            service=service,
            created_by=request.user,
            status__in=[
                PreviewEnvironment.Status.BUILDING,
                PreviewEnvironment.Status.READY,
                PreviewEnvironment.Status.HEALTH_CHECK_RUNNING,
            ],
        ).count()
        if existing >= MAX_PREVIEWS_PER_CREATOR:
            return Response(
                {"error": f"Per-user preview quota exceeded ({existing}/{MAX_PREVIEWS_PER_CREATOR}). Destroy existing previews first."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        serializer = PreviewCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        branch_name = serializer.validated_data['branch_name']
        commit_sha = serializer.validated_data['commit_sha']

        manager = BranchPreviewManager()
        preview = manager.create_preview(service, branch_name, commit_sha, user=request.user)

        from apps.deployments.tasks.deployment.tasks_safedeploy import create_preview_environment_job
        create_preview_environment_job.delay(str(preview.id))

        serializer = self.get_serializer(preview)

        try:
            AuditLog(
                actor=request.user.get_username(),
                action='PREVIEW_CREATED',
                target=f'preview={preview.id}',
                metadata={'service': str(service.id), 'branch': branch_name, 'commit_sha': commit_sha},
            ).save()
        except Exception as exc:
            logger.warning("Failed to write PREVIEW_CREATED audit log: %s", exc)

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @throttle_classes([PreviewThrottle])
    @action(detail=True, methods=['post'])
    def rebuild(self, request, pk=None, service_pk=None):
        preview = self.get_object()
        assert_can_write(self.request.user, preview.service, action='rebuild preview')

        if preview.status == PreviewEnvironment.Status.DESTROYING:
            return Response(
                {"error": "Preview is currently being destroyed, cannot rebuild"},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = PreviewRebuildSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        commit_sha = serializer.validated_data.get('commit_sha', preview.commit_sha)

        manager = BranchPreviewManager()
        updated_preview = manager.rebuild_preview(preview, commit_sha)

        from apps.deployments.tasks.deployment.tasks_safedeploy import create_preview_environment_job
        create_preview_environment_job.delay(str(updated_preview.id))

        serializer = self.get_serializer(updated_preview)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @throttle_classes([PreviewThrottle])
    @action(detail=True, methods=['post'], url_path='destroy_preview')
    def destroy_preview(self, request, pk=None, service_pk=None):
        preview = self.get_object()
        assert_can_write(self.request.user, preview.service, action='destroy preview')

        if preview.status == PreviewEnvironment.Status.BUILDING:
            return Response(
                {"error": "Preview is currently building, cannot destroy until build completes"},
                status=status.HTTP_409_CONFLICT,
            )

        manager = BranchPreviewManager()
        manager.destroy_preview(preview)

        from apps.deployments.tasks.deployment.tasks_safedeploy import destroy_preview_environment_job
        destroy_preview_environment_job.delay(str(preview.id))

        try:
            AuditLog(
                actor=request.user.get_username(),
                action='PREVIEW_DESTROYED',
                target=f'preview={preview.id}',
                metadata={'service': str(preview.service_id)},
            ).save()
        except Exception as exc:
            logger.warning("Failed to write PREVIEW_DESTROYED audit log: %s", exc)

        return Response({"status": "destroying"}, status=status.HTTP_202_ACCEPTED)


class DeploymentApprovalViewSet(viewsets.ModelViewSet):
    """
    API for approving/rejecting production deployments.
    """
    queryset = DeploymentApproval.objects.all()
    serializer_class = DeploymentApprovalSerializer
    # Use CsrfExemptSessionAuthentication for the session fallback: these
    # endpoints are called from the same-origin frontend which authenticates
    # via the HttpOnly auth cookie. The cookie is already a strong
    # same-origin credential (SameSite=Lax/Strict) and the endpoints
    # require an explicit permission check (CanManagePreviews) on top of
    # authentication, so CSRF adds friction without meaningful protection.
    # Token-based callers (CLI, automations) still get CSRF enforcement
    # via the default TokenAuthentication path.
    authentication_classes = [
        authentication.TokenAuthentication,
        CsrfExemptSessionAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated, CanApproveDeployment]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            # Defensive: same pattern as ServiceViewSet.get_queryset
            # (views.py:756-759) and PreviewEnvironmentViewSet above. The
            # viewset default IsAuthenticated keeps this unreachable in
            # normal flow, but a future public @action on this viewset
            # would otherwise raise TypeError on the FK lookup below.
            return DeploymentApproval.objects.none()
        service_id = self.kwargs.get('service_pk')
        qs = DeploymentApproval.objects.filter(
            Q(service__owner=user) | Q(service__project__team__members__user=user)
        ).distinct()
        if service_id:
            return qs.filter(service_id=service_id)
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        service_id = self.kwargs.get('service_pk')
        if service_id:
            qs = qs.filter(service_id=service_id)
        serializer = self.get_serializer(qs.order_by('-created_at'), many=True)
        return Response(serializer.data)

    def _get_approval_for_service(self, pk, service_pk):
        try:
            return DeploymentApproval.objects.get(id=pk, service_id=service_pk)
        except DeploymentApproval.DoesNotExist:
            return None

    @throttle_classes([ApprovalThrottle])
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None, service_pk=None):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        with transaction.atomic():
            try:
                approval = DeploymentApproval.objects.select_for_update().get(
                    id=pk, service_id=service_pk,
                )
            except DeploymentApproval.DoesNotExist:
                return Response({"error": "Approval not found"}, status=status.HTTP_404_NOT_FOUND)

            assert_can_write(self.request.user, approval.service, action='approve deployment')

            if approval.status != DeploymentApproval.Status.PENDING:
                return Response(
                    {"error": f"Approval is in {approval.status} status, not PENDING."},
                    status=status.HTTP_409_CONFLICT,
                )

            deployment = approval.deployment
            if deployment is None:
                return Response({"error": "No deployment associated with this approval"}, status=status.HTTP_400_BAD_REQUEST)

            if deployment.status != Deployment.Status.AWAITING_APPROVAL:
                return Response({"error": "Deployment is not awaiting approval"}, status=status.HTTP_400_BAD_REQUEST)

            if str(deployment.service_id) != str(service_pk):
                return Response({"error": "Deployment does not belong to this service"}, status=status.HTTP_400_BAD_REQUEST)

            pipeline = ProductionDeploymentPipeline()
            approval = pipeline.approve_and_process(deployment, request.user)

        try:
            AuditLog(
                actor=request.user.get_username(),
                action='DEPLOYMENT_APPROVED',
                target=f'approval={approval.id}',
                metadata={'deployment': str(deployment.id), 'service': str(service_pk), 'risk_level': approval.risk_level},
            ).save()
        except Exception as exc:
            logger.warning("Failed to write DEPLOYMENT_APPROVED audit log: %s", exc)

        try:
            send_approval_notification(approval, service_pk)
        except Exception as exc:
            logger.warning("Failed to send approval notification: %s", exc)

        return Response({"status": "approved", "approval_id": str(approval.id)}, status=status.HTTP_200_OK)

    @throttle_classes([ApprovalThrottle])
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None, service_pk=None):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        approval = self._get_approval_for_service(pk, service_pk)
        if approval is None:
            return Response({"error": "Approval not found"}, status=status.HTTP_404_NOT_FOUND)
        assert_can_write(self.request.user, approval.service, action='reject deployment')

        deployment = approval.deployment
        if deployment is None:
            return Response({"error": "No deployment associated with this approval"}, status=status.HTTP_400_BAD_REQUEST)

        if deployment.status != Deployment.Status.AWAITING_APPROVAL:
            return Response({"error": "Deployment is not awaiting approval"}, status=status.HTTP_400_BAD_REQUEST)

        if str(deployment.service_id) != str(service_pk):
            return Response({"error": "Deployment does not belong to this service"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ApprovalRejectSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        notes = serializer.validated_data.get("notes", "")
        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.reject_deployment(deployment, request.user, notes)

        try:
            AuditLog(
                actor=request.user.get_username(),
                action='DEPLOYMENT_REJECTED',
                target=f'approval={approval.id}',
                metadata={'deployment': str(deployment.id), 'service': str(service_pk), 'risk_level': approval.risk_level},
            ).save()
        except Exception as exc:
            logger.warning("Failed to write DEPLOYMENT_REJECTED audit log: %s", exc)

        try:
            send_approval_notification(approval, service_pk)
        except Exception as exc:
            logger.warning("Failed to send rejection notification: %s", exc)

        return Response({"status": "rejected", "approval_id": str(approval.id)}, status=status.HTTP_200_OK)

    @throttle_classes([ApprovalThrottle])
    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None, service_pk=None):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )
        approval = self._get_approval_for_service(pk, service_pk)
        if approval is None:
            return Response({"error": "Approval not found"}, status=status.HTTP_404_NOT_FOUND)
        deployment = approval.deployment
        if deployment is None:
            return Response({"error": "No deployment associated with this approval"}, status=status.HTTP_400_BAD_REQUEST)
        if deployment.status not in (Deployment.Status.FAILED, Deployment.Status.MIGRATION_FAILED, Deployment.Status.ROLLED_BACK):
            return Response({"error": f"Cannot retry deployment in {deployment.status} status."}, status=status.HTTP_409_CONFLICT)
        deployment.status = Deployment.Status.MIGRATION_PLANNING
        deployment.save()
        pipeline = ProductionDeploymentPipeline()
        pipeline.process_deployment(deployment)
        return Response({"status": deployment.status}, status=status.HTTP_200_OK)
