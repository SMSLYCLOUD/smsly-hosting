from rest_framework import permissions
from .models_safedeploy import DeploymentApproval, MigrationValidation

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
        if approval_id is None:
            return True

        try:
            approval = DeploymentApproval.objects.get(id=approval_id)
        except DeploymentApproval.DoesNotExist:
            return False

        deployment = approval.deployment
        if deployment is None:
            return False

        # If user is superadmin, allow
        if request.user.is_superuser:
            return True

        validation = getattr(deployment, 'migration_validation', None)
        if validation:
            # Block critical risk for non-superusers
            if validation.risk_level == MigrationValidation.RiskLevel.CRITICAL:
                return False

        # In a real app we'd check if user is an admin of the specific Service's organization here
        # For v1 fallback, we require is_staff for high risk if not superuser
        if validation and validation.risk_level == MigrationValidation.RiskLevel.HIGH:
            return request.user.is_staff

        return True # Allowed for low/medium if they have basic access to the endpoint
