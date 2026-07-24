import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import PermissionDenied

from apps.deployments.models import (
    Addon,
    Deployment,
    EnvironmentVariable,
    ManagedServer,
    Project,
    Service,
)
from apps.deployments.tasks import smart_deploy_task
from apps.teams.permissions import (
    assert_can_delete,
    assert_can_write,
    get_team_q_filter,
    user_can_read,
    user_is_team_admin,
)

logger = logging.getLogger(__name__)


def _resolve_user(user_id: str | None = None, user_email: str | None = None):
    """Resolve user from ID or email for RBAC permission checks."""
    if not user_id and not user_email:
        return None
    User = get_user_model()
    if user_id:
        try:
            return User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise PermissionDenied(f"User with ID '{user_id}' not found.")
    if user_email:
        try:
            return User.objects.get(email=user_email)
        except ObjectDoesNotExist:
            raise PermissionDenied(f"User with email '{user_email}' not found.")
    return None


def list_services(user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all deployed ecosystem services and their current status."""
    try:
        user = _resolve_user(user_id, user_email)
        services = Service.objects.all()
        if user:
            services = services.filter(get_team_q_filter(user))

        results = []
        for svc in services:
            latest_deploy = svc.deployments.order_by('-created_at').first()
            results.append({
                "id": str(svc.id),
                "name": svc.name,
                "buildpack": getattr(svc, "buildpack", "DOCKER"),
                "status": getattr(svc, "status", "UNKNOWN"),
                "latest_deployment_id": str(latest_deploy.id) if latest_deploy else None,
                "repository_url": getattr(svc, "repository_url", None),
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list services: {e!s}"}]


def get_deployment_status(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Get detailed status, stage timings, and commit hash for a deployment."""
    try:
        user = _resolve_user(user_id, user_email)
        deploy = Deployment.objects.get(id=deployment_id)
        if user and deploy.service and not user_can_read(user, deploy.service):
            return {"error": "Permission denied: You do not have read access to this deployment's service."}

        return {
            "id": str(deploy.id),
            "service_name": deploy.service.name if deploy.service else "Unknown",
            "status": deploy.status,
            "commit_hash": getattr(deploy, "commit_hash", ""),
            "created_at": str(deploy.created_at),
            "stages": getattr(deploy, "stages", {}),
            "error_message": getattr(deploy, "error_message", None),
        }
    except ObjectDoesNotExist:
        return {"error": f"Deployment {deployment_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def get_service_logs(service_id: str, lines: int = 50, user_id: str | None = None, user_email: str | None = None) -> str:
    """Fetch the latest deployment or runtime logs for a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return "Error: Permission denied. You do not have read access to this service."

        latest_deploy = svc.deployments.order_by('-created_at').first()
        if not latest_deploy:
            return "No deployments found for this service."

        log_content = getattr(latest_deploy, "build_logs", "") or getattr(latest_deploy, "runtime_logs", "")
        if not log_content:
            return "No logs recorded for latest deployment."

        log_lines = log_content.splitlines()
        return "\n".join(log_lines[-lines:])
    except ObjectDoesNotExist:
        return f"Error: Service {service_id} not found."
    except Exception as e:
        return f"Error: {e!s}"


def get_service_env_vars(service_id: str, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """Get environment variables for a service. Secret values are masked."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return [{"error": "Permission denied: You do not have read access to this service."}]

        vars_list = []
        for ev in svc.env_vars.all():
            vars_list.append({
                "key": ev.key,
                "value": "********" if ev.is_secret else ev.value,
                "is_secret": ev.is_secret,
                "is_locked": ev.is_locked,
                "source": ev.source
            })
        return vars_list
    except ObjectDoesNotExist:
        return [{"error": f"Service {service_id} not found."}]
    except Exception as e:
        return [{"error": f"Error: {e!s}"}]


def set_service_env_var(service_id: str, key: str, value: str, is_secret: bool = False, user_id: str | None = None, user_email: str | None = None) -> dict[str, str]:
    """Set or update an environment variable for a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_write(user, svc, action='modify environment variables')

        ev, created = EnvironmentVariable.objects.update_or_create(
            service=svc,
            key=key,
            defaults={
                "value": value,
                "is_secret": is_secret,
                "source": "USER_MCP"
            }
        )
        return {"status": "created" if created else "updated", "key": ev.key}
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Permission denied or error: {e!s}"}


def delete_service_env_var(service_id: str, key: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, str]:
    """Delete an environment variable from a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_delete(user, svc, action='delete environment variable')

        deleted_count, _ = svc.env_vars.filter(key=key).delete()
        if deleted_count > 0:
            return {"status": "deleted", "key": key}
        return {"error": f"Key {key} not found on service {service_id}."}
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Permission denied or error: {e!s}"}


def trigger_service_rebuild(service_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Trigger an automated deployment rebuild for a service (auto-remediation)."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_write(user, svc, action='trigger deployment rebuild')
            if not user_is_team_admin(user, svc):
                return {"error": "Only team admins can trigger rebuilds."}

        new_deploy = Deployment.objects.create(
            service=svc,
            status="QUEUED",
            commit_hash=getattr(svc, "latest_commit", "latest")
        )
        provider_id = getattr(svc, "provider_id", "local")
        smart_deploy_task.delay(str(new_deploy.id), str(provider_id), skip_review=True)
        return {
            "status": "rebuild_triggered",
            "deployment_id": str(new_deploy.id),
            "service": svc.name
        }
    except Exception as e:
        return {"error": f"Failed to trigger rebuild: {e!s}"}


def get_error_diagnostics(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Analyze deployment failure logs and suggest auto-remediation actions."""
    try:
        user = _resolve_user(user_id, user_email)
        deploy = Deployment.objects.get(id=deployment_id)
        if user and deploy.service and not user_can_read(user, deploy.service):
            return {"error": "Permission denied: You do not have read access to this deployment's service."}

        if deploy.status != "FAILED":
            return {"status": deploy.status, "message": "Deployment did not fail. No remediation needed."}

        logs = getattr(deploy, "build_logs", "") or ""
        error_summary = []
        remediation = "Inspect logs manually."

        if "out of memory" in logs.lower() or "oom" in logs.lower():
            error_summary.append("Out of Memory (OOM) error detected during build.")
            remediation = "Increase container swap space or upgrade RAM allocation."
        elif "no dockerfile was found" in logs.lower():
            error_summary.append("Dockerfile missing for DOCKER build strategy.")
            remediation = "Switch buildpack to NIXPACKS or STATIC using MCP set tools, then trigger rebuild."
        elif "permission denied" in logs.lower():
            error_summary.append("File permission error during container setup.")
            remediation = "Check build script execution permissions."
        else:
            error_summary.append("Generic build failure.")

        return {
            "deployment_id": str(deploy.id),
            "status": "FAILED",
            "error_summary": error_summary,
            "suggested_remediation": remediation,
            "can_auto_rebuild": True
        }
    except ObjectDoesNotExist:
        return {"error": f"Deployment {deployment_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def list_projects(user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all projects/workspaces in the ecosystem."""
    try:
        user = _resolve_user(user_id, user_email)
        projects = Project.objects.all()
        if user:
            projects = projects.filter(get_team_q_filter(user))

        results = []
        for proj in projects:
            results.append({
                "id": str(proj.id),
                "name": proj.name,
                "slug": proj.slug,
                "description": proj.description,
                "is_default": proj.is_default,
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list projects: {e!s}"}]


def get_project_services(project_id: str, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """Get all services deployed within a specific project."""
    try:
        user = _resolve_user(user_id, user_email)
        proj = Project.objects.get(id=project_id)
        if user and not user_can_read(user, proj):
            return [{"error": "Permission denied: You do not have read access to this project."}]

        services = Service.objects.filter(project=proj)
        results = []
        for svc in services:
            latest_deploy = svc.deployments.order_by('-created_at').first()
            results.append({
                "id": str(svc.id),
                "name": svc.name,
                "buildpack": getattr(svc, "buildpack", "DOCKER"),
                "status": getattr(svc, "status", "UNKNOWN"),
                "latest_deployment_id": str(latest_deploy.id) if latest_deploy else None,
            })
        return results
    except ObjectDoesNotExist:
        return [{"error": f"Project {project_id} not found."}]
    except Exception as e:
        return [{"error": f"Error: {e!s}"}]


def bulk_import_env_vars(service_id: str, env_vars: dict[str, str], is_secret: bool = False, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Import multiple environment variables or secrets at once into a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_write(user, svc, action='bulk import environment variables')

        updated_keys = []
        for key, value in env_vars.items():
            ev, _ = EnvironmentVariable.objects.update_or_create(
                service=svc,
                key=key,
                defaults={
                    "value": str(value),
                    "is_secret": is_secret,
                    "source": "USER_MCP"
                }
            )
            updated_keys.append(ev.key)
        return {"status": "bulk_imported", "count": len(updated_keys), "keys": updated_keys}
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Permission denied or error: {e!s}"}


def list_service_addons(service_id: str, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all databases, caches, and storage addons attached to a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return [{"error": "Permission denied: You do not have read access to this service."}]

        results = []
        for addon in svc.addons.all():
            results.append({
                "id": str(addon.id),
                "name": addon.name,
                "addon_type": addon.addon_type,
                "status": addon.status,
                "has_connection_url": bool(addon.connection_url),
            })
        return results
    except ObjectDoesNotExist:
        return [{"error": f"Service {service_id} not found."}]
    except Exception as e:
        return [{"error": f"Error: {e!s}"}]


def provision_service_addon(service_id: str, addon_type: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Trigger automated provisioning of an addon (POSTGRES, REDIS, MONGODB, etc.) for a service."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_write(user, svc, action='provision database addon')

        from apps.addons.services.addon_provisioner import addon_provisioner
        addon_type_upper = addon_type.upper()
        if addon_type_upper not in dict(Addon.Type.choices):
            return {"error": f"Unsupported addon type: {addon_type}. Valid options: {list(dict(Addon.Type.choices).keys())}"}

        addon, _ = Addon.objects.get_or_create(
            service=svc,
            addon_type=addon_type_upper,
            defaults={
                "name": f"{addon_type_upper.lower()}-{svc.name}"[:255],
                "status": Addon.Status.PROVISIONING
            }
        )
        _, url = addon_provisioner.provision_dispatch(addon)
        if url:
            addon.connection_url = url
            addon.status = Addon.Status.ACTIVE
            addon.save()

            env_key = addon_provisioner.ENV_KEY_MAP.get(addon_type_upper, f"{addon_type_upper}_URL")
            EnvironmentVariable.objects.update_or_create(
                service=svc, key=env_key,
                defaults={'value': url, 'is_secret': True, 'source': 'MCP_ADDON'}
            )
            return {"status": "provisioned", "addon_id": str(addon.id), "env_key": env_key}
        return {"status": getattr(addon, "status", "UNKNOWN"), "addon_id": str(addon.id), "message": "Provisioning dispatched."}
    except Exception as e:
        return {"error": f"Permission denied or addon provisioning failed: {e!s}"}


def get_exhaustive_deployment_diagnostics(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Parse and return structured telemetry from the 9 exhaustive logging pillars."""
    try:
        user = _resolve_user(user_id, user_email)
        deploy = Deployment.objects.get(id=deployment_id)
        if user and deploy.service and not user_can_read(user, deploy.service):
            return {"error": "Permission denied: You do not have read access to this deployment's service."}

        logs = getattr(deploy, "build_logs", "") or getattr(deploy, "runtime_logs", "") or ""

        pillars = {
            "clone_diagnostics": "Not found in logs",
            "env_diagnostics": "Not found in logs",
            "build_diagnostics": "Not found in logs",
            "push_diagnostics": "Not found in logs",
            "network_diagnostics": "Not found in logs",
            "addon_diagnostics": "Not found in logs",
            "runtime_diagnostics": "Not found in logs",
            "remote_diagnostics": "Not found in logs",
            "self_heal_diagnostics": "Not found in logs",
        }

        for line in logs.splitlines():
            if "[GIT SOURCE TREE & CLONE OPERATIONAL METRICS]" in line:
                pillars["clone_diagnostics"] = "Present & Verified ✅"
            elif "[ENVIRONMENT INJECTION & SECURITY AUDIT]" in line:
                pillars["env_diagnostics"] = "Present & Verified ✅"
            elif "[BUILD ENGINE & WORKSPACE PREPARATION]" in line:
                pillars["build_diagnostics"] = "Present & Verified ✅"
            elif "[CONTAINER REGISTRY PUSH & VULNERABILITY GATE]" in line:
                pillars["push_diagnostics"] = "Present & Verified ✅"
            elif "[NETWORK TOPOLOGY, PROXY ROUTING & SSL TERMINATION]" in line:
                pillars["network_diagnostics"] = "Present & Verified ✅"
            elif "[DATABASE & CACHE ADDON PROVISIONING MESH]" in line:
                pillars["addon_diagnostics"] = "Present & Verified ✅"
            elif "[RUNTIME ACTIVATION, BLUE-GREEN PROMOTION & HEALTH MESH]" in line:
                pillars["runtime_diagnostics"] = "Present & Verified ✅"
            elif "[REMOTE NODE ORCHESTRATION & DELEGATION TELEMETRY]" in line:
                pillars["remote_diagnostics"] = "Present & Verified ✅"
            elif "[AUTONOMOUS SELF-HEALING & AI REMEDIATION TELEMETRY]" in line:
                pillars["self_heal_diagnostics"] = "Present & Verified ✅"

        return {
            "deployment_id": str(deploy.id),
            "status": deploy.status,
            "exhaustive_pillars_status": pillars,
            "total_log_lines": len(logs.splitlines())
        }
    except ObjectDoesNotExist:
        return {"error": f"Deployment {deployment_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def list_managed_servers(user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all cloud nodes and servers in the cluster with their online status."""
    try:
        user = _resolve_user(user_id, user_email)
        servers = ManagedServer.objects.all()
        if user and not user.is_superuser:
            servers = servers.filter(owner=user)

        results = []
        for srv in servers:
            results.append({
                "id": str(srv.id),
                "name": srv.name,
                "host": srv.host,
                "status": srv.status,
                "provision_status": srv.provision_status,
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list servers: {e!s}"}]


def get_server_health(server_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Get detailed health and provisioning status for a managed cluster server."""
    try:
        user = _resolve_user(user_id, user_email)
        srv = ManagedServer.objects.get(id=server_id)
        if user and not user.is_superuser and srv.owner != user:
            return {"error": "Permission denied: Only server owners or platform admins can view server diagnostics."}

        return {
            "id": str(srv.id),
            "name": srv.name,
            "host": srv.host,
            "status": srv.status,
            "provision_status": srv.provision_status,
            "error_message": getattr(srv, "error_message", None),
        }
    except ObjectDoesNotExist:
        return {"error": f"Server {server_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def deploy_from_local_archive(service_id: str, file_path: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Deploy a service directly from a local source code archive (.zip, .tar.gz, .tgz)."""
    try:
        import os
        from pathlib import Path
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user:
            assert_can_write(user, svc, action='deploy from local file archive')

        if not os.path.exists(file_path):
            return {"error": f"Local file archive not found at path: {file_path}"}

        filename_lower = file_path.lower()
        if not (filename_lower.endswith('.zip') or filename_lower.endswith('.tar.gz') or filename_lower.endswith('.tgz')):
            return {"error": "Invalid archive format. Allowed extensions: .zip, .tar.gz, .tgz"}

        file_size = os.path.getsize(file_path)
        if file_size > 100 * 1024 * 1024:
            return {"error": f"Archive size ({file_size / 1024 / 1024:.1f}MB) exceeds 100MB limit."}

        svc.deploy_type = 'UPLOAD'
        svc.repository_url = Path(file_path).resolve().as_uri()
        svc.save(update_fields=['deploy_type', 'repository_url', 'updated_at'])

        new_deploy = Deployment.objects.create(
            service=svc,
            status=Deployment.Status.QUEUED,
            commit_hash=f"mcp-upload-{os.path.basename(file_path)[:15]}",
            commit_message=f"MCP Local Archive Deploy: {os.path.basename(file_path)}"
        )
        provider_id = getattr(svc, "provider_id", "local")
        smart_deploy_task.delay(str(new_deploy.id), str(provider_id), skip_review=True)

        return {
            "status": "deployment_triggered",
            "deployment_id": str(new_deploy.id),
            "service": svc.name,
            "archive_uri": svc.repository_url,
            "deploy_type": "UPLOAD"
        }
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Failed to deploy from local archive: {e!s}"}

