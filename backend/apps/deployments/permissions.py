import logging

from rest_framework import permissions

from .models.core import Service
from .models.safedeploy import DeploymentApproval, MigrationValidation

logger = logging.getLogger(__name__)


class CanManagePreviews(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            logger.warning("CanManagePreviews denied: unauthenticated request to service_pk=%s", view.kwargs.get('service_pk'))
            return False

        service_id = view.kwargs.get('service_pk')
        if service_id is None:
            return True

        try:
            service = Service.objects.select_related('owner', 'project__team').get(id=service_id)
        except Service.DoesNotExist:
            return True

        allowed = self._user_can_access_service(request.user, service)
        if not allowed:
            logger.warning(
                "CanManagePreviews denied: user=%s service=%s owner=%s project=%s",
                request.user.id, service_id, service.owner_id, service.project_id,
            )
        return allowed

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        allowed = self._user_can_access_service(request.user, obj.service)
        if not allowed:
            logger.warning(
                "CanManagePreviews object denied: user=%s preview=%s service=%s owner=%s",
                request.user.id, obj.id, obj.service_id, obj.service.owner_id,
            )
        return allowed

    def _user_can_access_service(self, user, service):
        if user.is_superuser:
            return True
        if service.owner == user:
            return True
        team = getattr(service.project, 'team', None)
        return bool(team and team.members.filter(user=user).exists())


class CanApproveDeployment(permissions.BasePermission):
    """
    Permission check for SafeDeploy approvals.
    Super-admins can approve any risk.
    Regular admins can approve up to HIGH risk (depending on org policy).
    Normal users can approve LOW/MEDIUM risk if they have deploy access.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        approval_id = view.kwargs.get('pk')
        service_pk = view.kwargs.get('service_pk')

        if approval_id is None:
            return True

        try:
            if service_pk:
                approval = DeploymentApproval.objects.get(id=approval_id, service_id=service_pk)
            else:
                approval = DeploymentApproval.objects.get(id=approval_id)
        except DeploymentApproval.DoesNotExist:
            return not service_pk

        deployment = approval.deployment
        if deployment is None:
            return False

        if service_pk and str(deployment.service_id) != str(service_pk):
            return False

        service = approval.service

        if not self._user_can_access_service(request.user, service):
            return False

        if approval.requested_by_id == request.user.id:
            logger.warning(
                "CanApproveDeployment denied self-approval: user=%s approval=%s service=%s",
                request.user.id, approval_id, service_pk,
            )
            return False

        if request.user.is_superuser:
            return True

        validation = getattr(deployment, 'migration_validation', None)
        if validation:
            if validation.risk_level == MigrationValidation.RiskLevel.CRITICAL:
                return False

        if validation and validation.risk_level == MigrationValidation.RiskLevel.HIGH:
            return request.user.is_staff

        return True

    def _user_can_access_service(self, user, service):
        if user.is_superuser:
            return True
        if service.owner == user:
            return True
        team = getattr(service.project, 'team', None)
        return bool(team and team.members.filter(user=user).exists())
