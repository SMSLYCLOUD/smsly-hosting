# pylint: disable=invalid-name
"""
Celery tasks for ecosystem-level deployment.

Pipeline:
1. Scan repositories -> generate deploy plan.
2. Deploy plan -> create/update services, apply env vars, queue deployments.
"""

import logging
import re
import secrets
import string
from typing import Any, Dict

from celery import shared_task

logger = logging.getLogger(__name__)

_SECRET_HINTS = ("KEY", "SECRET", "PASSWORD", "TOKEN", "DSN")
_EXTERNAL_SECRETS = {
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "STRIPE_SECRET_KEY",
    "API_KEY",
}


def _generate_secret(length: int = 50) -> str:
    """Generate a secure random string for env vars."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _slugify_name(raw: str) -> str:
    """Normalize a service name to a docker-safe slug."""
    name = re.sub(r"[^a-zA-Z0-9-]+", "-", str(raw or "").strip())
    name = re.sub(r"-{2,}", "-", name).strip("-").lower()
    return name or "service"


def _next_available_service_name(ServiceModel, base_name: str) -> str:
    """Find a globally unique service name."""
    if not ServiceModel.objects.filter(name=base_name).exists():
        return base_name

    for _ in range(20):
        candidate = f"{base_name}-{secrets.token_hex(2)}"
        if not ServiceModel.objects.filter(name=candidate).exists():
            return candidate

    return f"{base_name}-{secrets.token_hex(4)}"


def _normalize_env_vars(raw_env: Any) -> Dict[str, str]:
    """
    Accept flexible env var formats from AI/heuristics and normalize to dict.

    Supported:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    normalized: Dict[str, str] = {}

    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            key_text = str(key or "").strip().upper()
            if not key_text:
                continue
            normalized[key_text] = "" if value is None else str(value)
        return normalized

    if isinstance(raw_env, list):
        for entry in raw_env:
            if not isinstance(entry, dict):
                continue
            key_text = str(entry.get("key") or "").strip().upper()
            if not key_text:
                continue

            default_val = entry.get("default")
            if default_val not in (None, ""):
                normalized[key_text] = str(default_val)
                continue

            if key_text in _EXTERNAL_SECRETS or key_text.endswith("_API_KEY"):
                normalized[key_text] = ""
                continue

            if entry.get("generate") or entry.get("is_secret"):
                normalized[key_text] = "{{GENERATE}}"
                continue

            normalized[key_text] = ""

    return normalized


def _resolve_env_placeholders(
    env_vars: Dict[str, str],
    created_services: Dict[str, Any],
) -> Dict[str, str]:
    """Resolve known placeholders into concrete values."""
    resolved: Dict[str, str] = {}

    for key, value in env_vars.items():
        value_text = str(value or "")

        if value_text == "{{GENERATE}}":
            resolved[key] = _generate_secret()
            continue

        if value_text.startswith("{{SERVICE:") and value_text.endswith("}}"):
            ref_name = value_text[10:-2].strip()
            ref_service = created_services.get(ref_name)
            if ref_service:
                host = ref_service.name
                port = ref_service.internal_port or 3000
                resolved[key] = f"http://{host}:{port}"
            else:
                safe_ref = _slugify_name(ref_name)
                resolved[key] = f"http://{safe_ref}:3000"
            continue

        if value_text == "{{POSTGRES_URL}}":
            resolved[key] = "postgresql://smsly:smsly@postgres:5432/smsly"
            continue

        if value_text == "{{REDIS_URL}}":
            resolved[key] = "redis://redis:6379/0"
            continue

        resolved[key] = value_text

    return resolved


def _runtime_watch_defaults(user) -> Dict[str, str]:
    """Default zero-click runtime monitoring configuration."""
    defaults = {
        "JULES_RUNTIME_WATCH": "true",
        "JULES_NOTIFY_IN_APP": "true",
        "JULES_NOTIFY_SMS": "true",
        "JULES_NOTIFY_EMAIL": "true",
        "JULES_NOTIFY_TELEGRAM": "false",
        "JULES_NOTIFY_WHATSAPP": "false",
    }
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        defaults["ALERT_EMAIL"] = email
    return defaults


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def ecosystem_scan_task(self, user_id: str) -> dict:
    """
    Scan all of a user's GitHub repos and return a deploy plan.
    This is async because fetching and AI analysis can take 30-60s.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.views_github import _get_github_token
    from services.ecosystem import scan_and_analyze

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    token = _get_github_token(user)
    if not token:
        return {"error": "GitHub not connected. Please link your GitHub account first."}

    try:
        return scan_and_analyze(token)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Ecosystem scan failed for user %s: %s", user_id, exc)
        return {"error": f"Scan failed: {str(exc)}"}


@shared_task(bind=True, soft_time_limit=900, time_limit=1200)
def ecosystem_deploy_task(self, user_id: str, plan: dict) -> dict:
    """
    Deploy all services in the plan, in dependency order.

    This creates Service + Deployment records for each repo and triggers
    individual deployments via smart_deploy_task.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.models import Service, Deployment, EnvironmentVariable
    from apps.cloud.models import CloudProvider

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    if not isinstance(plan, dict):
        return {"error": "Invalid plan payload"}

    services_plan = plan.get("services", [])
    if not isinstance(services_plan, list) or not services_plan:
        return {"error": "No services in deploy plan"}

    provider = CloudProvider.objects.filter(is_active=True).first() or CloudProvider.objects.first()
    if not provider:
        return {"error": "No cloud provider configured. Add one in Settings -> Cloud Providers."}

    def _order_key(item: Any) -> int:
        if not isinstance(item, dict):
            return 99
        try:
            return int(item.get("deploy_order", 99))
        except (TypeError, ValueError):
            return 99

    services_plan.sort(key=_order_key)

    results = []
    created_services: Dict[str, Any] = {}

    for svc_plan in services_plan:
        if not isinstance(svc_plan, dict):
            continue

        if svc_plan.get("skip"):
            results.append({"repo": svc_plan.get("repo", ""), "status": "skipped"})
            continue

        repo = str(svc_plan.get("repo") or "").strip()
        if not repo:
            results.append({"repo": "", "status": "failed", "error": "Missing repo"})
            continue

        source_name = str(svc_plan.get("name") or repo.split("/")[-1]).strip()
        requested_name = _slugify_name(source_name)
        try:
            port = int(svc_plan.get("port", 3000) or 3000)
        except (TypeError, ValueError):
            port = 3000
        stack = str(svc_plan.get("stack") or "unknown")
        build_method = str(svc_plan.get("build") or "nixpacks")

        try:
            service = Service.objects.filter(owner=user, name=requested_name).first()
            if service is None:
                final_name = _next_available_service_name(Service, requested_name)
                service = Service.objects.create(
                    name=final_name,
                    owner=user,
                    repository_url=f"https://github.com/{repo}",
                    branch="main",
                    internal_port=port,
                    provider=provider,
                )
            else:
                service.repository_url = f"https://github.com/{repo}"
                service.branch = "main"
                service.internal_port = port
                if not service.provider:
                    service.provider = provider
                service.save()

            # Keep multiple aliases for inter-service references.
            created_services[source_name] = service
            created_services[requested_name] = service
            created_services[service.name] = service
            created_services[repo.split("/")[-1]] = service

            env_vars = _normalize_env_vars(svc_plan.get("env_vars", {}))
            resolved_env = _resolve_env_placeholders(env_vars, created_services)
            for key, value in _runtime_watch_defaults(user).items():
                resolved_env.setdefault(key, value)

            for key, value in resolved_env.items():
                key_upper = str(key or "").strip().upper()
                if not key_upper:
                    continue
                is_secret = any(hint in key_upper for hint in _SECRET_HINTS)
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key_upper,
                    defaults={"value": str(value or ""), "is_secret": is_secret},
                )

            deployment = Deployment.objects.create(
                service=service,
                commit_hash="ecosystem-deploy",
                commit_message=f"Zero-config ecosystem deploy ({stack})",
                status=Deployment.Status.QUEUED,
                build_logs=(
                    f"Ecosystem deploy: {repo} ({stack})\n"
                    f"Port: {port} | Build: {build_method}\n"
                    f"Env vars: {len(resolved_env)} configured\n\n"
                ),
            )

            self.app.send_task(
                "apps.deployments.tasks.smart_deploy_task",
                args=[str(deployment.id), str(provider.id)],
            )

            results.append({
                "repo": repo,
                "name": service.name,
                "service_id": str(service.id),
                "deployment_id": str(deployment.id),
                "status": "queued",
                "stack": stack,
                "port": port,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to deploy %s: %s", repo, exc)
            results.append({
                "repo": repo,
                "name": requested_name,
                "status": "failed",
                "error": str(exc),
            })

    return {
        "status": "deploying",
        "total": len(services_plan),
        "queued": len([r for r in results if r["status"] == "queued"]),
        "skipped": len([r for r in results if r["status"] == "skipped"]),
        "failed": len([r for r in results if r["status"] == "failed"]),
        "services": results,
    }
