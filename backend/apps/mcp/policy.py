"""Project-scope policy for MCP tool calls (shared by both transports).

Token scopes (read/write) gate WHAT a token may do; the project list
gates WHERE: which projects' services/deployments/addons it may touch.
An empty project list means legacy full access (all owner's projects).

Fail-closed throughout: unresolvable targets, missing projects, and
cross-project list tools are denied for bound tokens — except
``list_projects``, which is filtered to the allowed set.
"""
import logging

logger = logging.getLogger(__name__)

# Tools taking a direct project_id argument.
_PROJECT_ID_TOOLS = frozenset({"get_project_services"})

# Tools taking a service_id argument (resolved via Service.project_id).
_SERVICE_ID_TOOLS = frozenset({
    "get_service_logs",
    "get_service_env_vars",
    "set_service_env_var",
    "delete_service_env_var",
    "trigger_service_rebuild",
    "bulk_import_env_vars",
    "list_service_addons",
    "provision_service_addon",
    "deploy_from_local_archive",
    "get_service_details",
    "list_service_deployments",
    "get_service_domains",
})

# Tools taking a deployment_id argument (via Deployment.service).
_DEPLOYMENT_ID_TOOLS = frozenset({
    "get_deployment_status",
    "get_error_diagnostics",
    "get_exhaustive_deployment_diagnostics",
    "cancel_deployment",
    "retry_deployment",
})

# Tools taking an addon_id argument (via Addon.service).
_ADDON_ID_TOOLS = frozenset({"get_addon_details"})

# Cross-project or platform-level tools: no single target to check.
# Bound tokens are denied these (use get_project_services for discovery),
# except list_projects, which is filtered to the allowed set instead.
_GLOBAL_TOOLS = frozenset({
    "list_services",
    "list_projects",
    "search_services",
    "list_managed_servers",
    "get_server_health",
    "get_failed_deployments",
    "list_all_addons",
})


def _service_project_id(service_id) -> str | None:
    """Project ID string for a service, or None when unresolvable."""
    try:
        from apps.deployments.models import Service
        svc = Service.objects.only("project_id").get(id=service_id)
        return str(svc.project_id) if svc.project_id else None
    except Exception:
        return None


def _deployment_project_id(deployment_id) -> str | None:
    try:
        from apps.deployments.models import Deployment
        dep = Deployment.objects.select_related("service").get(id=deployment_id)
        svc = getattr(dep, "service", None)
        pid = getattr(svc, "project_id", None)
        return str(pid) if pid else None
    except Exception:
        return None


def _addon_project_id(addon_id) -> str | None:
    try:
        from apps.deployments.models.addons import Addon
        addon = Addon.objects.select_related("service").get(id=addon_id)
        svc = getattr(addon, "service", None)
        pid = getattr(svc, "project_id", None)
        return str(pid) if pid else None
    except Exception:
        return None


def resolve_tool_project(tool_name: str, args: dict) -> str | None:
    """Project ID string this call targets, or None if global/unresolvable.

    None means "no checkable target" — callers deny bound tokens on None
    EXCEPT list_projects (filtered instead). Never returns "".
    """
    args = args or {}
    if tool_name in _PROJECT_ID_TOOLS:
        pid = args.get("project_id")
        return str(pid).strip() if pid else None
    if tool_name in _SERVICE_ID_TOOLS:
        sid = args.get("service_id")
        return _service_project_id(sid) if sid else None
    if tool_name in _DEPLOYMENT_ID_TOOLS:
        did = args.get("deployment_id")
        return _deployment_project_id(did) if did else None
    if tool_name in _ADDON_ID_TOOLS:
        aid = args.get("addon_id")
        return _addon_project_id(aid) if aid else None
    return None


def check_project_scope(token, tool_name: str, args: dict) -> str | None:
    """Error string when ``token`` may not call ``tool_name`` with ``args``.

    Returns None when allowed. Unbound tokens (legacy full access) always
    pass. Unknown tools default-deny for bound tokens (fail closed).
    """
    scope = token.project_scope() if hasattr(token, "project_scope") else None
    if scope is None:
        return None
    if tool_name == "list_projects":
        return None  # filtered to the allowed set, never denied
    if tool_name not in (
        _PROJECT_ID_TOOLS | _SERVICE_ID_TOOLS | _DEPLOYMENT_ID_TOOLS | _ADDON_ID_TOOLS
    ):
        return (
            f"Token is project-scoped; tool '{tool_name}' has no single "
            "project target. Use get_project_services for discovery."
        )
    pid = resolve_tool_project(tool_name, args or {})
    if not pid:
        return (
            f"Token is project-scoped; target of '{tool_name}' could not "
            "be resolved to a project."
        )
    if pid not in scope:
        return (
            f"Token is not granted to this project for tool '{tool_name}'."
        )
    return None


def filter_projects_by_scope(scope: list | None, result):
    """Filter a list_projects result to ``scope`` (None = full access)."""
    if scope is None or not isinstance(result, list):
        return result
    allowed = set(scope)
    return [p for p in result if not isinstance(p, dict) or p.get("id") in allowed
            or "error" in p]


def filter_projects_result(token, result):
    """Filter a list_projects result to the token's allowed projects."""
    scope = token.project_scope() if hasattr(token, "project_scope") else None
    return filter_projects_by_scope(scope, result)
