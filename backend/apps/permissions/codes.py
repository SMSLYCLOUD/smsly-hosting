"""Permission code constants for smsly-hosting RBAC.

Every permission check references one of these module-level string constants.
The constants are grouped by resource domain.
"""

# ── Service ──
SERVICE_CREATE = "service.create"
SERVICE_UPDATE = "service.update"
SERVICE_DELETE = "service.delete"
SERVICE_DEPLOY = "service.deploy"
SERVICE_STOP = "service.stop"
SERVICE_RESTART = "service.restart"

# ── Deployment ──
DEPLOYMENT_VIEW = "deployment.view"
DEPLOYMENT_CREATE = "deployment.create"
DEPLOYMENT_APPROVE = "deployment.approve"
DEPLOYMENT_ROLLBACK = "deployment.rollback"

# ── Domain / Env ──
DOMAIN_MANAGE = "domain.manage"
ENV_VAR_MANAGE = "env_var.manage"

# ── Addons ──
ADDON_MANAGE = "addon.manage"

# ── Billing ──
BILLING_VIEW = "billing.view"
BILLING_MANAGE = "billing.manage"
BILLING_ADMIN = "billing.admin"

# ── Team / Org ──
TEAM_MANAGE = "team.manage"
MEMBER_INVITE = "member.invite"
MEMBER_REMOVE = "member.remove"
MEMBER_ROLE_CHANGE = "member.role_change"

# ── Project ──
PROJECT_CREATE = "project.create"
PROJECT_DELETE = "project.delete"
PROJECT_MANAGE = "project.manage"

# ── Settings / Admin ──
SETTINGS_MANAGE = "settings.manage"
ADMIN_ACCESS = "admin.access"
AUDIT_VIEW = "audit.view"
ANALYTICS_VIEW = "analytics.view"

# ── Aggregate sets ──
ALL_PERMISSIONS: set[str] = {
    SERVICE_CREATE, SERVICE_UPDATE, SERVICE_DELETE, SERVICE_DEPLOY,
    SERVICE_STOP, SERVICE_RESTART,
    DEPLOYMENT_VIEW, DEPLOYMENT_CREATE, DEPLOYMENT_APPROVE, DEPLOYMENT_ROLLBACK,
    DOMAIN_MANAGE, ENV_VAR_MANAGE, ADDON_MANAGE,
    BILLING_VIEW, BILLING_MANAGE, BILLING_ADMIN,
    TEAM_MANAGE, MEMBER_INVITE, MEMBER_REMOVE, MEMBER_ROLE_CHANGE,
    PROJECT_CREATE, PROJECT_DELETE, PROJECT_MANAGE,
    SETTINGS_MANAGE, ADMIN_ACCESS, AUDIT_VIEW, ANALYTICS_VIEW,
}

# ── Default role-to-permission mapping ──
# These represent the baseline permissions for each team role.
# Per-member overrides (TeamMember.permissions JSONField) and project-level
# roles (ProjectMember) can further refine this.
DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "ADMIN": [
        SERVICE_CREATE, SERVICE_UPDATE, SERVICE_DELETE, SERVICE_DEPLOY,
        SERVICE_STOP, SERVICE_RESTART,
        DEPLOYMENT_VIEW, DEPLOYMENT_CREATE, DEPLOYMENT_APPROVE, DEPLOYMENT_ROLLBACK,
        DOMAIN_MANAGE, ENV_VAR_MANAGE, ADDON_MANAGE,
        BILLING_VIEW,
        TEAM_MANAGE, MEMBER_INVITE, MEMBER_REMOVE, MEMBER_ROLE_CHANGE,
        PROJECT_CREATE, PROJECT_DELETE, PROJECT_MANAGE,
        SETTINGS_MANAGE, AUDIT_VIEW, ANALYTICS_VIEW,
    ],
    "MEMBER": [
        SERVICE_CREATE, SERVICE_UPDATE, SERVICE_DEPLOY, SERVICE_STOP, SERVICE_RESTART,
        DEPLOYMENT_VIEW, DEPLOYMENT_CREATE, DEPLOYMENT_ROLLBACK,
        DOMAIN_MANAGE, ENV_VAR_MANAGE, ADDON_MANAGE,
        BILLING_VIEW,
        AUDIT_VIEW, ANALYTICS_VIEW,
    ],
    "VIEWER": [
        DEPLOYMENT_VIEW,
        BILLING_VIEW,
        AUDIT_VIEW, ANALYTICS_VIEW,
    ],
}
