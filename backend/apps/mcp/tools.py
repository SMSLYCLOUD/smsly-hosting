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
        except (ObjectDoesNotExist, ValueError):
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


def search_services(query: str, status: str | None = None, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """Search services by name, slug, or repository URL, optionally filtered by status."""
    try:
        from django.db.models import Q
        user = _resolve_user(user_id, user_email)
        services = Service.objects.filter(
            Q(name__icontains=query) | Q(slug__icontains=query) | Q(repository_url__icontains=query)
        )
        if status:
            services = services.filter(status=status.upper())
        if user:
            services = services.filter(get_team_q_filter(user))

        results = []
        for svc in services[:50]:
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
        return [{"error": f"Failed to search services: {e!s}"}]


def get_service_details(service_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Get full service detail: config, resources, HA mode, domains, and recent deployments."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return {"error": "Permission denied: You do not have read access to this service."}

        recent = []
        for deploy in svc.deployments.order_by('-created_at')[:5]:
            recent.append({
                "id": str(deploy.id),
                "status": deploy.status,
                "commit_hash": getattr(deploy, "commit_hash", ""),
                "branch": getattr(deploy, "branch", ""),
                "created_at": str(deploy.created_at),
            })
        return {
            "id": str(svc.id),
            "name": svc.name,
            "slug": getattr(svc, "slug", ""),
            "status": getattr(svc, "status", "UNKNOWN"),
            "buildpack": getattr(svc, "buildpack", "DOCKER"),
            "deploy_type": getattr(svc, "deploy_type", "GIT"),
            "repository_url": getattr(svc, "repository_url", None),
            "branch": getattr(svc, "branch", "main"),
            "docker_image": getattr(svc, "docker_image", None),
            "internal_port": getattr(svc, "internal_port", 8000),
            "public_domain": getattr(svc, "public_domain", None),
            "staging_domain": getattr(svc, "staging_domain", None),
            "cpu_cores": float(getattr(svc, "cpu_cores", 1.0) or 1.0),
            "memory_mb": getattr(svc, "memory_mb", 2048),
            "min_replicas": getattr(svc, "min_replicas", 1),
            "max_replicas": getattr(svc, "max_replicas", 1),
            "running_replicas": getattr(svc, "running_replicas", 0),
            "ha_mode": getattr(svc, "ha_mode", "none"),
            "project": svc.project.name if getattr(svc, "project", None) else None,
            "server": svc.server.name if getattr(svc, "server", None) else None,
            "domains_count": svc.domain_instances.count() if hasattr(svc, "domain_instances") else 0,
            "addons_count": svc.addons.count(),
            "env_vars_count": svc.env_vars.count(),
            "recent_deployments": recent,
        }
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def list_service_deployments(service_id: str, limit: int = 10, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List deployment history for a service, newest first."""
    try:
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return [{"error": "Permission denied: You do not have read access to this service."}]

        limit = max(1, min(int(limit), 50))
        results = []
        for deploy in svc.deployments.order_by('-created_at')[:limit]:
            duration = None
            if getattr(deploy, "started_at", None) and getattr(deploy, "finished_at", None):
                duration = (deploy.finished_at - deploy.started_at).total_seconds()
            results.append({
                "id": str(deploy.id),
                "status": deploy.status,
                "commit_hash": getattr(deploy, "commit_hash", ""),
                "commit_message": (getattr(deploy, "commit_message", "") or "")[:200],
                "branch": getattr(deploy, "branch", ""),
                "is_rollback": getattr(deploy, "is_rollback", False),
                "created_at": str(deploy.created_at),
                "duration_seconds": duration,
            })
        return results
    except ObjectDoesNotExist:
        return [{"error": f"Service {service_id} not found."}]
    except Exception as e:
        return [{"error": f"Error: {e!s}"}]


def cancel_deployment(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Cancel a QUEUED, REVIEW, BUILDING, AWAITING_APPROVAL, or STAGED deployment (team admin only)."""
    try:
        from django.utils import timezone
        user = _resolve_user(user_id, user_email)
        deploy = Deployment.objects.get(id=deployment_id)
        if user:
            if not deploy.service:
                return {"error": "Deployment has no service; cannot check permissions."}
            assert_can_write(user, deploy.service, action='cancel deployment')
            if not user_is_team_admin(user, deploy.service):
                return {"error": "Only team admins can cancel deployments."}

        if deploy.status not in (
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.AWAITING_APPROVAL,
            Deployment.Status.STAGED,
        ):
            return {"error": f"Cannot cancel deployment in {deploy.status} status. Only QUEUED, REVIEW, BUILDING, AWAITING_APPROVAL, or STAGED deployments can be cancelled."}

        deploy.status = Deployment.Status.CANCELLED
        deploy.finished_at = timezone.now()
        deploy.build_logs = f"{deploy.build_logs or ''}\n\n[Cancelled] Deployment cancelled via MCP."
        try:
            if deploy.green_container_id or deploy.container_id:
                import docker
                client = docker.from_env()
                for c_id in {deploy.green_container_id, deploy.container_id} - {None, ""}:
                    try:
                        client.containers.get(c_id).remove(force=True)
                    except Exception:
                        pass
                deploy.build_logs += "\nCleaned up container resources."
        except Exception as exc:
            logger.warning("MCP cancel docker cleanup failed for %s: %s", deployment_id, exc)
        try:
            from apps.deployments.tasks.deploy.helpers import _regenerate_caddyfile
            _regenerate_caddyfile()
        except Exception as exc:
            logger.warning("MCP cancel caddy regen failed for %s: %s", deployment_id, exc)
        deploy.save()
        return {"status": "cancelled", "deployment_id": str(deploy.id)}
    except ObjectDoesNotExist:
        return {"error": f"Deployment {deployment_id} not found."}
    except Exception as e:
        return {"error": f"Permission denied or error: {e!s}"}


def retry_deployment(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Re-queue a FAILED or CANCELLED deployment (team admin only)."""
    try:
        from django.utils import timezone
        user = _resolve_user(user_id, user_email)
        deploy = Deployment.objects.get(id=deployment_id)
        if user:
            if not deploy.service:
                return {"error": "Deployment has no service; cannot check permissions."}
            assert_can_write(user, deploy.service, action='retry deployment')
            if not user_is_team_admin(user, deploy.service):
                return {"error": "Only team admins can retry deployments."}

        if deploy.status not in (Deployment.Status.FAILED, Deployment.Status.CANCELLED):
            return {"error": f"Cannot retry deployment in {deploy.status} status. Only FAILED or CANCELLED deployments can be retried."}

        deploy.status = Deployment.Status.QUEUED
        deploy.build_logs = (
            f"{deploy.build_logs or ''}"
            f"\n[MCP] Re-queued by user retry at {timezone.now().isoformat()}.\n"
        )
        deploy.save(update_fields=['status', 'build_logs', 'updated_at'])
        provider_id = getattr(deploy.service, "provider_id", "local") if deploy.service else "local"
        smart_deploy_task.delay(str(deploy.id), str(provider_id), skip_review=True)
        return {"status": "retry_queued", "deployment_id": str(deploy.id)}
    except ObjectDoesNotExist:
        return {"error": f"Deployment {deployment_id} not found."}
    except Exception as e:
        return {"error": f"Permission denied or error: {e!s}"}


def get_failed_deployments(limit: int = 10, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List recent failed deployments across services with a log excerpt for triage."""
    try:
        user = _resolve_user(user_id, user_email)
        failed_statuses = [
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.HEALTH_CHECK_FAILED,
        ]
        deploys = Deployment.objects.filter(status__in=failed_statuses).order_by('-created_at')
        if user:
            deploys = deploys.filter(service__in=Service.objects.filter(get_team_q_filter(user)))

        limit = max(1, min(int(limit), 50))
        results = []
        for deploy in deploys[:limit]:
            if user and deploy.service and not user_can_read(user, deploy.service):
                continue
            logs = getattr(deploy, "build_logs", "") or getattr(deploy, "runtime_logs", "") or ""
            excerpt_lines = [ln for ln in logs.splitlines() if ln.strip()][-3:]
            results.append({
                "deployment_id": str(deploy.id),
                "service_id": str(deploy.service.id) if deploy.service else None,
                "service_name": deploy.service.name if deploy.service else "Unknown",
                "status": deploy.status,
                "commit_hash": getattr(deploy, "commit_hash", ""),
                "created_at": str(deploy.created_at),
                "ai_diagnosis": (getattr(deploy, "ai_diagnosis", "") or "")[:500] or None,
                "log_excerpt": "\n".join(excerpt_lines)[-1500:],
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list failed deployments: {e!s}"}]


def list_all_addons(status: str | None = None, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all addons across services, optionally filtered by status."""
    try:
        user = _resolve_user(user_id, user_email)
        addons = Addon.objects.select_related("service").all()
        if status:
            addons = addons.filter(status=status.upper())
        if user:
            addons = addons.filter(service__in=Service.objects.filter(get_team_q_filter(user)))

        results = []
        for addon in addons[:100]:
            if user and addon.service and not user_can_read(user, addon.service):
                continue
            results.append({
                "id": str(addon.id),
                "name": addon.name,
                "addon_type": addon.addon_type,
                "status": addon.status,
                "service_id": str(addon.service.id) if addon.service else None,
                "service_name": addon.service.name if addon.service else None,
                "ha_enabled": getattr(addon, "ha_enabled", False),
                "ha_status": getattr(addon, "ha_status", "DISABLED"),
                "has_connection_url": bool(addon.connection_url),
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list addons: {e!s}"}]


def get_addon_details(addon_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Get addon detail including HA state and connection info with the password masked."""
    try:
        from urllib.parse import urlparse
        user = _resolve_user(user_id, user_email)
        addon = Addon.objects.select_related("service").get(id=addon_id)
        if user and addon.service and not user_can_read(user, addon.service):
            return {"error": "Permission denied: You do not have read access to this addon's service."}

        masked_url, scheme, host, port, database, has_password = None, None, None, None, None, False
        raw_url = addon.connection_url or ""
        if raw_url:
            try:
                parsed = urlparse(raw_url)
                scheme = parsed.scheme or None
                host = parsed.hostname
                port = parsed.port
                database = (parsed.path or "").lstrip("/") or None
                has_password = bool(parsed.password)
                netloc = parsed.hostname or ""
                if parsed.port:
                    netloc += f":{parsed.port}"
                if parsed.username:
                    netloc = f"{parsed.username}:********@{netloc}"
                masked_url = parsed._replace(netloc=netloc).geturl()
            except Exception:
                masked_url = None

        return {
            "id": str(addon.id),
            "name": addon.name,
            "addon_type": addon.addon_type,
            "status": addon.status,
            "service_id": str(addon.service.id) if addon.service else None,
            "service_name": addon.service.name if addon.service else None,
            "ha_enabled": getattr(addon, "ha_enabled", False),
            "ha_status": getattr(addon, "ha_status", "DISABLED"),
            "replica_container_name": getattr(addon, "replica_container_name", "") or None,
            "deletion_error": getattr(addon, "deletion_error", "") or None,
            "connection_scheme": scheme,
            "connection_host": host,
            "connection_port": port,
            "connection_database": database,
            "connection_has_password": has_password,
            "connection_url_masked": masked_url,
        }
    except ObjectDoesNotExist:
        return {"error": f"Addon {addon_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}


def get_service_domains(service_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Get platform, staging, custom, and managed domains for a service with SSL/verification state."""
    try:
        from apps.domains.models.domain import Domain
        user = _resolve_user(user_id, user_email)
        svc = Service.objects.get(id=service_id)
        if user and not user_can_read(user, svc):
            return {"error": "Permission denied: You do not have read access to this service."}

        managed = []
        for dom in Domain.objects.filter(service=svc).order_by("domain_name"):
            managed.append({
                "id": dom.id,
                "domain_name": dom.domain_name,
                "status": dom.status,
                "verified": dom.verified,
                "ssl_active": dom.ssl_active,
                "ssl_fail_count": dom.ssl_fail_count,
                "expires_at": str(dom.expires_at) if dom.expires_at else None,
                "last_error": (dom.last_error or "")[:500] or None,
            })
        return {
            "service_id": str(svc.id),
            "service_name": svc.name,
            "public_domain": getattr(svc, "public_domain", None),
            "domain_verified": getattr(svc, "domain_verified", False),
            "staging_domain": getattr(svc, "staging_domain", None),
            "staging_domain_verified": getattr(svc, "staging_domain_verified", False),
            "custom_domains": getattr(svc, "custom_domains", []) or [],
            "managed_domains": managed,
        }
    except ObjectDoesNotExist:
        return {"error": f"Service {service_id} not found."}
    except Exception as e:
        return {"error": f"Error: {e!s}"}

