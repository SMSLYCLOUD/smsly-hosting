from rest_framework import viewsets, status, permissions, authentication
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from django.conf import settings as django_settings
from django.db import transaction
from django.db.models import Q
import logging
from apps.deployments.models_core import Service, Deployment
from apps.deployments.models_safedeploy import PreviewEnvironment, DeploymentApproval
from apps.deployments.models_audit import AuditLog
from apps.deployments.serializers import (
    PreviewEnvironmentSerializer, DeploymentApprovalSerializer,
    PreviewCreateSerializer, PreviewRebuildSerializer,
    ApprovalApproveSerializer, ApprovalRejectSerializer,
)
from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager
from apps.deployments.permissions import CanApproveDeployment, CanManagePreviews

logger = logging.getLogger(__name__)


class PreviewThrottle(UserRateThrottle):
    rate = '30/minute'


class ApprovalThrottle(UserRateThrottle):
    rate = '20/minute'


MAX_PREVIEWS_PER_CREATOR = getattr(django_settings, 'MAX_PREVIEWS_PER_CREATOR', 10)


def send_approval_notification(approval, service_pk):
    # TODO: wire to apps.notifications.services.send_notification once that helper exists.
    status_label = (approval.status or '').lower()
    requester = getattr(approval, 'requested_by', None)
    logger.info(
        "Would notify requester=%s service=%s approval=%s status=%s",
        getattr(requester, 'id', None), service_pk, getattr(approval, 'id', None), status_label,
    )


class PreviewEnvironmentViewSet(viewsets.ModelViewSet):
    queryset = PreviewEnvironment.objects.all()
    serializer_class = PreviewEnvironmentSerializer
    authentication_classes = [authentication.TokenAuthentication, authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated, CanManagePreviews]

    def _user_owns_or_member(self, service):
        if service.owner == self.request.user:
            return True
        team = getattr(service.project, 'team', None)
        if team and team.members.filter(user=self.request.user).exists():
            return True
        return False

    def get_queryset(self):
        service_id = self.kwargs.get('service_pk')
        qs = self.queryset.filter(
            Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user)
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

        flag_error = self._check_service_feature_flags(service)
        if flag_error:
            return flag_error

        existing = PreviewEnvironment.objects.filter(
            service=service,
            created_by=request.user,
        ).exclude(status__in=[
            PreviewEnvironment.Status.DESTROYED,
            PreviewEnvironment.Status.EXPIRED,
        ]).count()
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

        from apps.deployments.tasks_safedeploy import create_preview_environment_job
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

        from apps.deployments.tasks_safedeploy import create_preview_environment_job
        create_preview_environment_job.delay(str(updated_preview.id))

        serializer = self.get_serializer(updated_preview)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @throttle_classes([PreviewThrottle])
    @action(detail=True, methods=['post'], url_path='destroy_preview')
    def destroy_preview(self, request, pk=None, service_pk=None):
        preview = self.get_object()

        if preview.status == PreviewEnvironment.Status.BUILDING:
            return Response(
                {"error": "Preview is currently building, cannot destroy until build completes"},
                status=status.HTTP_409_CONFLICT,
            )

        manager = BranchPreviewManager()
        manager.destroy_preview(preview)

        from apps.deployments.tasks_safedeploy import destroy_preview_environment_job
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
    authentication_classes = [authentication.TokenAuthentication, authentication.SessionAuthentication]
    permission_classes = [permissions.IsAuthenticated, CanApproveDeployment]

    def get_queryset(self):
        service_id = self.kwargs.get('service_pk')
        qs = DeploymentApproval.objects.filter(
            Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user)
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
        from apps.deployments.services.safedeploy.deployment_pipeline import ProductionDeploymentPipeline

        with transaction.atomic():
            try:
                approval = DeploymentApproval.objects.select_for_update().get(
                    id=pk, service_id=service_pk,
                )
            except DeploymentApproval.DoesNotExist:
                return Response({"error": "Approval not found"}, status=status.HTTP_404_NOT_FOUND)

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
        from apps.deployments.services.safedeploy.deployment_pipeline import ProductionDeploymentPipeline

        approval = self._get_approval_for_service(pk, service_pk)
        if approval is None:
            return Response({"error": "Approval not found"}, status=status.HTTP_404_NOT_FOUND)

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
        from apps.deployments.services.safedeploy.deployment_pipeline import ProductionDeploymentPipeline
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
