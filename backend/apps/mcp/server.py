import logging

from mcp.server.fastmcp import FastMCP

from apps.mcp import tools

logger = logging.getLogger(__name__)

# Create the FastMCP server instance
mcp_server = FastMCP("SMSLY-Ecosystem-MCP")

# Register Tools with RBAC & Project Scoping Parameters
@mcp_server.tool()
def list_services(user_id: str = None, user_email: str = None):
    """List all deployed ecosystem services and their current status."""
    return tools.list_services(user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_deployment_status(deployment_id: str, user_id: str = None, user_email: str = None):
    """Get detailed status, stage timings, and commit hash for a deployment."""
    return tools.get_deployment_status(deployment_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_service_logs(service_id: str, lines: int = 50, user_id: str = None, user_email: str = None):
    """Fetch the latest deployment or runtime logs for a service."""
    return tools.get_service_logs(service_id, lines=lines, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_service_env_vars(service_id: str, user_id: str = None, user_email: str = None):
    """Get environment variables for a service. Secret values are masked."""
    return tools.get_service_env_vars(service_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def set_service_env_var(service_id: str, key: str, value: str, is_secret: bool = False, user_id: str = None, user_email: str = None):
    """Set or update an environment variable for a service."""
    return tools.set_service_env_var(service_id, key, value, is_secret=is_secret, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def delete_service_env_var(service_id: str, key: str, user_id: str = None, user_email: str = None):
    """Delete an environment variable from a service."""
    return tools.delete_service_env_var(service_id, key, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def trigger_service_rebuild(service_id: str, user_id: str = None, user_email: str = None):
    """Trigger an automated deployment rebuild for a service (auto-remediation)."""
    return tools.trigger_service_rebuild(service_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_error_diagnostics(deployment_id: str, user_id: str = None, user_email: str = None):
    """Analyze deployment failure logs and suggest auto-remediation actions."""
    return tools.get_error_diagnostics(deployment_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def list_projects(user_id: str = None, user_email: str = None):
    """List all projects/workspaces in the ecosystem."""
    return tools.list_projects(user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_project_services(project_id: str, user_id: str = None, user_email: str = None):
    """Get all services deployed within a specific project."""
    return tools.get_project_services(project_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def bulk_import_env_vars(service_id: str, env_vars: dict, is_secret: bool = False, user_id: str = None, user_email: str = None):
    """Import multiple environment variables or secrets at once into a service."""
    return tools.bulk_import_env_vars(service_id, env_vars, is_secret=is_secret, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def list_service_addons(service_id: str, user_id: str = None, user_email: str = None):
    """List all databases, caches, and storage addons attached to a service."""
    return tools.list_service_addons(service_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def provision_service_addon(service_id: str, addon_type: str, user_id: str = None, user_email: str = None):
    """Trigger automated provisioning of an addon (POSTGRES, REDIS, MONGODB, etc.) for a service."""
    return tools.provision_service_addon(service_id, addon_type, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_exhaustive_deployment_diagnostics(deployment_id: str, user_id: str = None, user_email: str = None):
    """Parse and return structured telemetry from the 9 exhaustive logging pillars."""
    return tools.get_exhaustive_deployment_diagnostics(deployment_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def list_managed_servers(user_id: str = None, user_email: str = None):
    """List all cloud nodes and servers in the cluster with their online status."""
    return tools.list_managed_servers(user_id=user_id, user_email=user_email)

@mcp_server.tool()
def get_server_health(server_id: str, user_id: str = None, user_email: str = None):
    """Get detailed health and provisioning status for a managed cluster server."""
    return tools.get_server_health(server_id, user_id=user_id, user_email=user_email)

@mcp_server.tool()
def deploy_from_local_archive(service_id: str, file_path: str, user_id: str = None, user_email: str = None):
    """Deploy a service directly from a local source code archive (.zip, .tar.gz, .tgz)."""
    return tools.deploy_from_local_archive(service_id, file_path, user_id=user_id, user_email=user_email)

