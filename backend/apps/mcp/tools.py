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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        services = Service.objects.filter(get_team_q_filter(user))

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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        deploy = Deployment.objects.get(id=deployment_id)
        if not deploy.service or not user_can_read(user, deploy.service):
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
        if not user:
            return "Error: Authentication required: caller identity missing."
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
            return "Error: Permission denied. You do not have read access to this service."

        latest_deploy = svc.deployments.order_by('-created_at').first()
        if not latest_deploy:
            return "No deployments found for this service."

        log_content = getattr(latest_deploy, "build_logs", "") or getattr(latest_deploy, "runtime_logs", "")
        if not log_content:
            return "No logs recorded for latest deployment."

        from apps.deployments.utils.log_scrub import mask_secrets_in_text
        scrubbed = mask_secrets_in_text(log_content) or ""
        log_lines = scrubbed.splitlines()
        return "\n".join(log_lines[-lines:])
    except ObjectDoesNotExist:
        return f"Error: Service {service_id} not found."
    except Exception as e:
        return f"Error: {e!s}"


def get_service_env_vars(service_id: str, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """Get environment variables for a service. Secret values are masked."""
    try:
        user = _resolve_user(user_id, user_email)
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
            return [{"error": "Permission denied: You do not have read access to this service."}]

        from apps.deployments.serializers.service import _must_mask_env_value
        vars_list = []
        for ev in svc.env_vars.all():
            masked = _must_mask_env_value(ev.key, ev.value, bool(ev.is_secret))
            vars_list.append({
                "key": ev.key,
                "value": "********" if masked else ev.value,
                "is_secret": bool(ev.is_secret or masked),
                "is_locked": ev.is_locked,
                "source": ev.source
            })
        return vars_list
    except ObjectDoesNotExist:
        return [{"error": f"Service {service_id} not found."}]
    except Exception as e:
        return [{"error": f"Error: {e!s}"}]


_DANGEROUS_ENV_KEYS = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "NODE_OPTIONS",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "BASH_ENV",
    "ENV",
    "PERL5OPT",
    "RUBYOPT",
    "DYLD_INSERT_LIBRARIES",
})


def _validate_and_scrub_env_entry(key: str, value: str) -> tuple[str | None, str | None, str | None]:
    """Validate an env var key and scrub/validate its value.

    Returns ``(clean_key, clean_value, error_message)``.
    """
    from apps.deployments.utils.env_sanitizer import is_placeholder, sanitize_env_value
    from apps.deployments.views._helpers import _is_valid_env_key

    clean_key = str(key or "").strip()
    if not clean_key or clean_key.upper() in _DANGEROUS_ENV_KEYS or not _is_valid_env_key(clean_key):
        return None, None, f"Environment variable key '{clean_key}' is invalid or restricted."

    cleaned = sanitize_env_value(value, key=clean_key, allow_empty=True)
    if cleaned is None or is_placeholder(cleaned):
        cleaned = ""
    if isinstance(cleaned, str) and cleaned.startswith("gAAAA"):
        return None, None, f"Environment variable '{clean_key}' appears to be encrypted ciphertext; supply plaintext."
    return clean_key, str(cleaned), None


def set_service_env_var(service_id: str, key: str, value: str, is_secret: bool = False, user_id: str | None = None, user_email: str | None = None) -> dict[str, str]:
    """Set or update an environment variable for a service."""
    try:
        clean_key, clean_value, err = _validate_and_scrub_env_entry(key, value)
        if err or not clean_key:
            return {"error": err or "Invalid environment variable key."}
        user = _resolve_user(user_id, user_email)
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        assert_can_write(user, svc, action='modify environment variables')

        if EnvironmentVariable.objects.filter(service=svc, key=clean_key, is_locked=True).exists():
            return {"error": f"Environment variable '{clean_key}' is locked and cannot be modified."}

        ev, created = EnvironmentVariable.objects.update_or_create(
            service=svc,
            key=clean_key,
            defaults={
                "value": clean_value,
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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        assert_can_delete(user, svc)

        if svc.env_vars.filter(key=key, is_locked=True).exists():
            return {"error": f"Environment variable '{key}' is locked and cannot be deleted."}

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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        assert_can_write(user, svc, action='trigger deployment rebuild')

        if getattr(svc, "require_deploy_approval", False):
            new_deploy = Deployment.objects.create(
                service=svc,
                status=Deployment.Status.AWAITING_APPROVAL,
                commit_hash=getattr(svc, "latest_commit", "latest")
            )
            return {
                "status": "awaiting_approval",
                "deployment_id": str(new_deploy.id),
                "service": svc.name,
            }

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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        deploy = Deployment.objects.get(id=deployment_id)
        if not deploy.service or not user_can_read(user, deploy.service):
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
        from django.db.models import Q
        user = _resolve_user(user_id, user_email)
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        projects = Project.objects.all()
        if not user.is_superuser:
            # Project-correct scoping: owned OR team-member. NOTE:
            # get_team_q_filter() builds service-shaped lookups
            # (project__team_id) which raise FieldError on Project
            # querysets — do not use it here.
            from apps.teams.models import TeamMember
            from django.utils import timezone
            team_ids = list(TeamMember.objects.filter(
                user=user, is_active=True,
            ).exclude(
                expires_at__isnull=False, expires_at__lt=timezone.now(),
            ).values_list('team_id', flat=True))
            projects = projects.filter(Q(owner=user) | Q(team_id__in=team_ids))

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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        proj = Project.objects.get(id=project_id)
        if not user_can_read(user, proj):
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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        assert_can_write(user, svc, action='bulk import environment variables')

        locked_keys = set(svc.env_vars.filter(is_locked=True).values_list("key", flat=True))
        validated_items: list[tuple[str, str]] = []
        for key, value in env_vars.items():
            clean_key, clean_value, err = _validate_and_scrub_env_entry(key, str(value))
            if err or not clean_key:
                return {"error": err or "Invalid environment variable."}
            if clean_key in locked_keys:
                return {"error": f"Environment variable '{clean_key}' is locked and cannot be modified."}
            validated_items.append((clean_key, clean_value or ""))

        updated_keys = []
        for clean_key, clean_value in validated_items:
            ev, _ = EnvironmentVariable.objects.update_or_create(
                service=svc,
                key=clean_key,
                defaults={
                    "value": clean_value,
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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
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
    """Queue automated provisioning of an addon (POSTGRES, REDIS, MONGODB, etc.) for a service."""
    try:
        from django.conf import settings
        from django.db import transaction
        from apps.addons.tasks.crud import provision_addon_task

        user = _resolve_user(user_id, user_email)
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        assert_can_write(user, svc, action='provision database addon')

        addon_type_upper = str(addon_type or "").strip().upper()
        if addon_type_upper not in dict(Addon.Type.choices):
            return {"error": f"Unsupported addon type: {addon_type}. Valid options: {list(dict(Addon.Type.choices).keys())}"}

        with transaction.atomic():
            if not bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False)):
                from apps.billing.services.metering import UsageMeter
                allowed, _ = UsageMeter().check_quota(user, "ADDON", requested_amount=1)
                if not allowed:
                    return {"error": "Addon quota exceeded for your current subscription plan."}

            addon, created = Addon.objects.get_or_create(
                service=svc,
                addon_type=addon_type_upper,
                defaults={
                    "name": f"{addon_type_upper.lower()}-{svc.name}"[:255],
                    "status": Addon.Status.PROVISIONING,
                },
            )
            if not created and addon.status == Addon.Status.ACTIVE:
                return {
                    "status": "already_active",
                    "addon_id": str(addon.id),
                    "addon_type": addon_type_upper,
                }
            if not created and addon.status != Addon.Status.PROVISIONING:
                addon.status = Addon.Status.PROVISIONING
                addon.save(update_fields=["status"])

        task = provision_addon_task.delay(addon_id=str(addon.id))
        return {
            "status": "provisioning_queued",
            "addon_id": str(addon.id),
            "addon_type": addon_type_upper,
            "task_id": getattr(task, "id", None),
            "message": "Addon provisioning queued asynchronously.",
        }
    except Exception as e:
        return {"error": f"Permission denied or addon provisioning failed: {e!s}"}


def get_exhaustive_deployment_diagnostics(deployment_id: str, user_id: str | None = None, user_email: str | None = None) -> dict[str, Any]:
    """Parse and return structured telemetry from the 9 exhaustive logging pillars."""
    try:
        user = _resolve_user(user_id, user_email)
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        deploy = Deployment.objects.get(id=deployment_id)
        if not deploy.service or not user_can_read(user, deploy.service):
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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        servers = ManagedServer.objects.all()
        if not user.is_superuser:
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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        srv = ManagedServer.objects.get(id=server_id)
        if not user.is_superuser and srv.owner != user:
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
        import tarfile
        import zipfile
        from pathlib import Path
        from django.conf import settings

        user = _resolve_user(user_id, user_email)
        if not user or not getattr(user, "is_active", False):
            return {"error": "Authentication required: a valid active user is required."}
        if not getattr(user, "is_superuser", False):
            return {"error": "Permission denied: deploy_from_local_archive requires superuser privileges."}

        svc = Service.objects.get(id=service_id)
        assert_can_write(user, svc, action='deploy from local file archive')

        raw_path = str(file_path or "").strip()
        filename_lower = raw_path.lower()
        if not (filename_lower.endswith('.zip') or filename_lower.endswith('.tar.gz') or filename_lower.endswith('.tgz')):
            return {"error": "Invalid archive format. Allowed extensions: .zip, .tar.gz, .tgz"}

        allowed_root = os.path.realpath("/app/var/uploads")
        real_path = os.path.realpath(raw_path)
        if not real_path.startswith(allowed_root + os.sep):
            return {"error": "Archive path must be inside /app/var/uploads."}

        if not os.path.isfile(real_path):
            return {"error": f"Local file archive not found at path: {raw_path}"}

        file_size = os.path.getsize(real_path)
        if file_size > 100 * 1024 * 1024:
            return {"error": f"Archive size ({file_size / 1024 / 1024:.1f}MB) exceeds 100MB limit."}

        max_uncompressed = 500 * 1024 * 1024
        if filename_lower.endswith('.zip'):
            with zipfile.ZipFile(real_path, 'r') as zf:
                total_uncompressed = 0
                for info in zf.infolist():
                    norm_name = info.filename.replace("\\", "/")
                    if norm_name.startswith("/") or ".." in norm_name.split("/"):
                        return {"error": f"Unsafe archive entry path: {info.filename}"}
                    if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                        return {"error": f"Symbolic links are not allowed in archive: {info.filename}"}
                    total_uncompressed += max(0, int(info.file_size))
                    if total_uncompressed > max_uncompressed:
                        return {"error": "Archive uncompressed size exceeds 500MB safety limit."}
        else:
            with tarfile.open(real_path, 'r:*') as tf:
                total_uncompressed = 0
                for member in tf.getmembers():
                    norm_name = member.name.replace("\\", "/")
                    if norm_name.startswith("/") or ".." in norm_name.split("/"):
                        return {"error": f"Unsafe archive entry path: {member.name}"}
                    if member.issym() or member.islnk() or member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                        return {"error": f"Special or link entries are not allowed in archive: {member.name}"}
                    total_uncompressed += max(0, int(member.size))
                    if total_uncompressed > max_uncompressed:
                        return {"error": "Archive uncompressed size exceeds 500MB safety limit."}

        svc.deploy_type = 'UPLOAD'
        svc.repository_url = Path(real_path).as_uri()
        svc.save(update_fields=['deploy_type', 'repository_url', 'updated_at'])

        new_deploy = Deployment.objects.create(
            service=svc,
            status=Deployment.Status.QUEUED,
            commit_hash=f"mcp-upload-{os.path.basename(real_path)[:15]}",
            commit_message=f"MCP Local Archive Deploy: {os.path.basename(real_path)}"
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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        services = Service.objects.filter(
            Q(name__icontains=query) | Q(slug__icontains=query) | Q(repository_url__icontains=query)
        )
        if status:
            services = services.filter(status=status.upper())
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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
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
    """Cancel a QUEUED, REVIEW, BUILDING, AWAITING_APPROVAL, or STAGED deployment."""
    try:
        from django.utils import timezone
        user = _resolve_user(user_id, user_email)
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        deploy = Deployment.objects.get(id=deployment_id)
        if not deploy.service:
            return {"error": "Deployment has no service; cannot check permissions."}
        assert_can_write(user, deploy.service, action='cancel deployment')

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
    """Re-queue a FAILED or CANCELLED deployment."""
    try:
        from django.utils import timezone
        user = _resolve_user(user_id, user_email)
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        deploy = Deployment.objects.get(id=deployment_id)
        if not deploy.service:
            return {"error": "Deployment has no service; cannot check permissions."}
        assert_can_write(user, deploy.service, action='retry deployment')

        if deploy.status not in (Deployment.Status.FAILED, Deployment.Status.CANCELLED):
            return {"error": f"Cannot retry deployment in {deploy.status} status. Only FAILED or CANCELLED deployments can be retried."}

        if getattr(deploy.service, "require_deploy_approval", False):
            deploy.status = Deployment.Status.AWAITING_APPROVAL
            deploy.build_logs = (
                f"{deploy.build_logs or ''}"
                f"\n[MCP] Retry awaiting approval at {timezone.now().isoformat()}.\n"
            )
            deploy.save(update_fields=['status', 'build_logs', 'updated_at'])
            return {"status": "awaiting_approval", "deployment_id": str(deploy.id)}

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
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        failed_statuses = [
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.HEALTH_CHECK_FAILED,
        ]
        deploys = Deployment.objects.filter(
            status__in=failed_statuses,
            service__in=Service.objects.filter(get_team_q_filter(user)),
        ).order_by('-created_at')

        limit = max(1, min(int(limit), 50))
        from apps.deployments.utils.log_scrub import mask_secrets_in_text
        results = []
        for deploy in deploys[:limit]:
            if user and deploy.service and not user_can_read(user, deploy.service):
                continue
            logs = getattr(deploy, "build_logs", "") or getattr(deploy, "runtime_logs", "") or ""
            scrubbed_logs = mask_secrets_in_text(logs) or ""
            excerpt_lines = [ln for ln in scrubbed_logs.splitlines() if ln.strip()][-3:]
            raw_diag = getattr(deploy, "ai_diagnosis", "") or ""
            scrubbed_diag = mask_secrets_in_text(raw_diag) or ""
            results.append({
                "deployment_id": str(deploy.id),
                "service_id": str(deploy.service.id) if deploy.service else None,
                "service_name": deploy.service.name if deploy.service else "Unknown",
                "status": deploy.status,
                "commit_hash": getattr(deploy, "commit_hash", ""),
                "created_at": str(deploy.created_at),
                "ai_diagnosis": scrubbed_diag[:500] or None,
                "log_excerpt": "\n".join(excerpt_lines)[-1500:],
            })
        return results
    except Exception as e:
        return [{"error": f"Failed to list failed deployments: {e!s}"}]


def list_all_addons(status: str | None = None, user_id: str | None = None, user_email: str | None = None) -> list[dict[str, Any]]:
    """List all addons across services, optionally filtered by status."""
    try:
        user = _resolve_user(user_id, user_email)
        if not user:
            return [{"error": "Authentication required: caller identity missing."}]
        addons = Addon.objects.select_related("service").filter(
            service__in=Service.objects.filter(get_team_q_filter(user))
        )
        if status:
            addons = addons.filter(status=status.upper())

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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        addon = Addon.objects.select_related("service").get(id=addon_id)
        if not addon.service or not user_can_read(user, addon.service):
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
                if parsed.username or has_password:
                    parsed_user = parsed.username or ""
                    netloc = f"{parsed_user}:********@{netloc}"
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
        if not user:
            return {"error": "Authentication required: caller identity missing."}
        svc = Service.objects.get(id=service_id)
        if not user_can_read(user, svc):
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

