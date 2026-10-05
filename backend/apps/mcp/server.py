import logging

try:
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    _MCP_AVAILABLE = True
except ImportError:
    Context = None  # type: ignore[assignment]
    ToolError = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

from apps.mcp import tools

logger = logging.getLogger(__name__)

def _mcp_auth(tool_name, ctx, user_id=None, user_email=None, tool_args=None):
    """Bind MCP-transport calls to a Bearer API token (fail-closed).

    HTTP transports (SSE / Streamable HTTP): require
    ``Authorization: Bearer smsly_...``. Identity comes from the token —
    caller-supplied user_id/user_email are ignored (they are spoofable).
    The token's scopes are enforced against the tool's required scope.

    Non-HTTP transports (stdio): local-process trust, equivalent to
    ``manage.py shell`` — the caller's explicit identity passes through.

    Returns (user_id, user_email, scope) where scope is None for
    full access or the allowed project-ID list for bound tokens.
    """
    request = None
    if ctx is not None:
        try:
            request = getattr(getattr(ctx, "request_context", None), "request", None)
        except Exception:
            request = None
    headers = getattr(request, "headers", None) if request is not None else None
    if headers is None:
        return user_id, user_email, None
    from apps.mcp.views import _required_scope
    raw = ""
    try:
        raw = str(headers.get("authorization", "") or "")
    except Exception:
        raw = ""
    scheme, _, credential = raw.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise ToolError(
            "MCP authentication required: send 'Authorization: Bearer smsly_...'."
        )
    from apps.core.models.api_token import APIToken
    try:
        user, token = APIToken.verify(credential.strip())
    except Exception:
        raise ToolError("Invalid or expired API token.") from None
    if not getattr(user, "is_active", False):
        raise ToolError("User account is disabled.")
    required = _required_scope(tool_name)
    if not token.has_scope(required):
        raise ToolError(
            f"Token lacks required scope '{required}' for tool '{tool_name}'."
        )
    from apps.mcp.policy import check_project_scope
    scope_error = check_project_scope(token, tool_name, tool_args or {})
    if scope_error:
        raise ToolError(scope_error)
    return str(user.id), getattr(user, "email", "") or "", token.project_scope()

if _MCP_AVAILABLE:
    # Create the FastMCP server instance
    mcp_server = FastMCP("SMSLY-Ecosystem-MCP")

    # Register Tools with RBAC & Project Scoping Parameters
    @mcp_server.tool()
    def list_services(user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List all deployed ecosystem services and their current status."""
        user_id, user_email, _scope = _mcp_auth('list_services', ctx, user_id, user_email)
        return tools.list_services(user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_deployment_status(deployment_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get detailed status, stage timings, and commit hash for a deployment."""
        user_id, user_email, _scope = _mcp_auth('get_deployment_status', ctx, user_id, user_email, tool_args={"deployment_id": deployment_id})
        return tools.get_deployment_status(deployment_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_service_logs(service_id: str, lines: int = 50, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Fetch the latest deployment or runtime logs for a service."""
        user_id, user_email, _scope = _mcp_auth('get_service_logs', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.get_service_logs(service_id, lines=lines, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_service_env_vars(service_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get environment variables for a service. Secret values are masked."""
        user_id, user_email, _scope = _mcp_auth('get_service_env_vars', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.get_service_env_vars(service_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def set_service_env_var(service_id: str, key: str, value: str, is_secret: bool = False, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Set or update an environment variable for a service."""
        user_id, user_email, _scope = _mcp_auth('set_service_env_var', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.set_service_env_var(service_id, key, value, is_secret=is_secret, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def delete_service_env_var(service_id: str, key: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Delete an environment variable from a service."""
        user_id, user_email, _scope = _mcp_auth('delete_service_env_var', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.delete_service_env_var(service_id, key, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def trigger_service_rebuild(service_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Trigger an automated deployment rebuild for a service (auto-remediation)."""
        user_id, user_email, _scope = _mcp_auth('trigger_service_rebuild', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.trigger_service_rebuild(service_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_error_diagnostics(deployment_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Analyze deployment failure logs and suggest auto-remediation actions."""
        user_id, user_email, _scope = _mcp_auth('get_error_diagnostics', ctx, user_id, user_email, tool_args={"deployment_id": deployment_id})
        return tools.get_error_diagnostics(deployment_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def list_projects(user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List all projects/workspaces in the ecosystem."""
        user_id, user_email, _scope = _mcp_auth('list_projects', ctx, user_id, user_email)
        from apps.mcp.policy import filter_projects_by_scope
        return filter_projects_by_scope(
            _scope, tools.list_projects(user_id=user_id, user_email=user_email))

    @mcp_server.tool()
    def get_project_services(project_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get all services deployed within a specific project."""
        user_id, user_email, _scope = _mcp_auth('get_project_services', ctx, user_id, user_email, tool_args={"project_id": project_id})
        return tools.get_project_services(project_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def bulk_import_env_vars(service_id: str, env_vars: dict, is_secret: bool = False, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Import multiple environment variables or secrets at once into a service."""
        user_id, user_email, _scope = _mcp_auth('bulk_import_env_vars', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.bulk_import_env_vars(service_id, env_vars, is_secret=is_secret, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def list_service_addons(service_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List all databases, caches, and storage addons attached to a service."""
        user_id, user_email, _scope = _mcp_auth('list_service_addons', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.list_service_addons(service_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def provision_service_addon(service_id: str, addon_type: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Trigger automated provisioning of an addon (POSTGRES, REDIS, MONGODB, etc.) for a service."""
        user_id, user_email, _scope = _mcp_auth('provision_service_addon', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.provision_service_addon(service_id, addon_type, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_exhaustive_deployment_diagnostics(deployment_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Parse and return structured telemetry from the 9 exhaustive logging pillars."""
        user_id, user_email, _scope = _mcp_auth('get_exhaustive_deployment_diagnostics', ctx, user_id, user_email, tool_args={"deployment_id": deployment_id})
        return tools.get_exhaustive_deployment_diagnostics(deployment_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def list_managed_servers(user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List all cloud nodes and servers in the cluster with their online status."""
        user_id, user_email, _scope = _mcp_auth('list_managed_servers', ctx, user_id, user_email)
        return tools.list_managed_servers(user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_server_health(server_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get detailed health and provisioning status for a managed cluster server."""
        user_id, user_email, _scope = _mcp_auth('get_server_health', ctx, user_id, user_email)
        return tools.get_server_health(server_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def deploy_from_local_archive(service_id: str, file_path: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Deploy a service directly from a local source code archive (.zip, .tar.gz, .tgz)."""
        user_id, user_email, _scope = _mcp_auth('deploy_from_local_archive', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.deploy_from_local_archive(service_id, file_path, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def search_services(query: str, status: str | None = None, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Search services by name, slug, or repository URL, optionally filtered by status."""
        user_id, user_email, _scope = _mcp_auth('search_services', ctx, user_id, user_email)
        return tools.search_services(query, status=status, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_service_details(service_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get full service detail: config, resources, HA mode, domains, and recent deployments."""
        user_id, user_email, _scope = _mcp_auth('get_service_details', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.get_service_details(service_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def list_service_deployments(service_id: str, limit: int = 10, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List deployment history for a service, newest first."""
        user_id, user_email, _scope = _mcp_auth('list_service_deployments', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.list_service_deployments(service_id, limit=limit, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def cancel_deployment(deployment_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Cancel a QUEUED, REVIEW, BUILDING, AWAITING_APPROVAL, or STAGED deployment (team admin only)."""
        user_id, user_email, _scope = _mcp_auth('cancel_deployment', ctx, user_id, user_email, tool_args={"deployment_id": deployment_id})
        return tools.cancel_deployment(deployment_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def retry_deployment(deployment_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Re-queue a FAILED or CANCELLED deployment (team admin only)."""
        user_id, user_email, _scope = _mcp_auth('retry_deployment', ctx, user_id, user_email, tool_args={"deployment_id": deployment_id})
        return tools.retry_deployment(deployment_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_failed_deployments(limit: int = 10, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List recent failed deployments across services with a log excerpt for triage."""
        user_id, user_email, _scope = _mcp_auth('get_failed_deployments', ctx, user_id, user_email)
        return tools.get_failed_deployments(limit=limit, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def list_all_addons(status: str | None = None, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """List all addons across services, optionally filtered by status."""
        user_id, user_email, _scope = _mcp_auth('list_all_addons', ctx, user_id, user_email)
        return tools.list_all_addons(status=status, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_addon_details(addon_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get addon detail including HA state and connection info with the password masked."""
        user_id, user_email, _scope = _mcp_auth('get_addon_details', ctx, user_id, user_email, tool_args={"addon_id": addon_id})
        return tools.get_addon_details(addon_id, user_id=user_id, user_email=user_email)

    @mcp_server.tool()
    def get_service_domains(service_id: str, user_id: str | None = None, user_email: str | None = None, ctx: Context | None = None):
        """Get platform, staging, custom, and managed domains for a service with SSL/verification state."""
        user_id, user_email, _scope = _mcp_auth('get_service_domains', ctx, user_id, user_email, tool_args={"service_id": service_id})
        return tools.get_service_domains(service_id, user_id=user_id, user_email=user_email)
else:
    mcp_server = None
    logger.warning("mcp.server.fastmcp not available — MCP server disabled (SDK v2 removed FastMCP)")
