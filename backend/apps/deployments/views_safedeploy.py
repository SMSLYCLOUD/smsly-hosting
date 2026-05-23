from rest_framework import viewsets, status, permissions, authentication
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from django.db.models import Q
from apps.deployments.models_core import Service, Deployment
from apps.deployments.models_safedeploy import PreviewEnvironment, DeploymentApproval
from apps.deployments.serializers import (
    PreviewEnvironmentSerializer, DeploymentApprovalSerializer,
    PreviewCreateSerializer, PreviewRebuildSerializer,
    ApprovalApproveSerializer, ApprovalRejectSerializer,
)
from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager
from apps.deployments.permissions import CanApproveDeployment, CanManagePreviews


class PreviewThrottle(UserRateThrottle):
    rate = '30/minute'


class ApprovalThrottle(UserRateThrottle):
    rate = '20/minute'


class PreviewEnvironmentViewSet(viewsets.ModelViewSet):
    queryset = PreviewEnvironment.objects.all()
    serializer_class = PreviewEnvironmentSerializer
    authentication_classes = [authentication.SessionAuthentication, authentication.TokenAuthentication]
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

        return Response({"status": "destroying"}, status=status.HTTP_202_ACCEPTED)


class DeploymentApprovalViewSet(viewsets.ModelViewSet):
    """
    API for approving/rejecting production deployments.
    """
    queryset = DeploymentApproval.objects.all()
    serializer_class = DeploymentApprovalSerializer
    authentication_classes = [authentication.SessionAuthentication, authentication.TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated, CanApproveDeployment]

    def get_queryset(self):
        service_id = self.kwargs.get('service_pk')
        qs = DeploymentApproval.objects.filter(
            Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user)
        ).distinct()
        if service_id:
            return qs.filter(service_id=service_id)
        return qs

    def _get_approval_for_service(self, pk, service_pk):
        try:
            return DeploymentApproval.objects.get(id=pk, service_id=service_pk)
        except DeploymentApproval.DoesNotExist:
            return None

    @throttle_classes([ApprovalThrottle])
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None, service_pk=None):
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

        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.approve_and_process(deployment, request.user)

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

        return Response({"status": "rejected", "approval_id": str(approval.id)}, status=status.HTTP_200_OK)
