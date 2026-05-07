from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.deployments.models_core import Service
from apps.deployments.models_safedeploy import PreviewEnvironment
from apps.deployments.serializers import PreviewEnvironmentSerializer, DeploymentApprovalSerializer
from apps.deployments.services.safedeploy.branch_preview_manager import BranchPreviewManager

class PreviewEnvironmentViewSet(viewsets.ModelViewSet):
    queryset = PreviewEnvironment.objects.all()
    serializer_class = PreviewEnvironmentSerializer # Will implement in a sec

    def get_queryset(self):
        service_id = self.kwargs.get('service_pk')
        if service_id:
            return self.queryset.filter(service_id=service_id)
        return self.queryset

    def create(self, request, *args, **kwargs):
        service_id = kwargs.get('service_pk')
        try:
            service = Service.objects.get(id=service_id)
        except Service.DoesNotExist:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)

        branch_name = request.data.get('branch_name')
        commit_sha = request.data.get('commit_sha')

        if not branch_name or not commit_sha:
            return Response({"error": "branch_name and commit_sha are required"}, status=status.HTTP_400_BAD_REQUEST)

        manager = BranchPreviewManager()
        preview = manager.create_preview(service, branch_name, commit_sha, user=request.user)

        # Trigger job
        from apps.deployments.tasks_safedeploy import create_preview_environment_job
        create_preview_environment_job.delay(str(preview.id))

        serializer = self.get_serializer(preview)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def rebuild(self, request, pk=None, service_pk=None):
        preview = self.get_object()
        commit_sha = request.data.get('commit_sha', preview.commit_sha)

        manager = BranchPreviewManager()
        updated_preview = manager.rebuild_preview(preview, commit_sha)

        from apps.deployments.tasks_safedeploy import create_preview_environment_job
        create_preview_environment_job.delay(str(updated_preview.id))

        serializer = self.get_serializer(updated_preview)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def destroy_preview(self, request, pk=None, service_pk=None):
        preview = self.get_object()

        manager = BranchPreviewManager()
        manager.destroy_preview(preview)

        from apps.deployments.tasks_safedeploy import destroy_preview_environment_job
        destroy_preview_environment_job.delay(str(preview.id))

        return Response({"status": "destroying"}, status=status.HTTP_202_ACCEPTED)


class DeploymentApprovalViewSet(viewsets.ModelViewSet):
    """
    API for approving/rejecting production deployments.
    """
    serializer_class = DeploymentApprovalSerializer
    from apps.deployments.permissions import CanApproveDeployment
    permission_classes = [CanApproveDeployment]

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None, service_pk=None):
        from apps.deployments.models_core import Deployment
        from apps.deployments.services.safedeploy.deployment_pipeline import ProductionDeploymentPipeline
        try:
            deployment = Deployment.objects.get(id=pk, service_id=service_pk)
        except Deployment.DoesNotExist:
            return Response({"error": "Deployment not found"}, status=status.HTTP_404_NOT_FOUND)

        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.approve_deployment(deployment, request.user)

        return Response({"status": "approved", "approval_id": str(approval.id)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None, service_pk=None):
        from apps.deployments.models_core import Deployment
        from apps.deployments.services.safedeploy.deployment_pipeline import ProductionDeploymentPipeline
        try:
            deployment = Deployment.objects.get(id=pk, service_id=service_pk)
        except Deployment.DoesNotExist:
            return Response({"error": "Deployment not found"}, status=status.HTTP_404_NOT_FOUND)

        notes = request.data.get("notes", "")
        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.reject_deployment(deployment, request.user, notes)

        return Response({"status": "rejected", "approval_id": str(approval.id)}, status=status.HTTP_200_OK)
