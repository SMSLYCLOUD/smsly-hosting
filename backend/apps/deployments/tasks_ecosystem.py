"""
Celery tasks for ecosystem-level deployment.

Handles the async pipeline: scan → analyze → deploy all services.
"""

import logging
import secrets
import string

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _generate_secret(length: int = 50) -> str:
    """Generate a secure random string for env vars."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@shared_task(bind=True, soft_time_limit=120, time_limit=180)
def ecosystem_scan_task(self, user_id: str) -> dict:
    """
    Scan all of a user's GitHub repos and return a deploy plan.
    This is async because fetching + AI analysis can take 30-60s.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.views_github import _get_github_token
    from services.ecosystem import scan_and_analyze

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return {"error": "User not found"}

    token = _get_github_token(user)
    if not token:
        return {"error": "GitHub not connected. Please link your GitHub account first."}

    try:
        plan = scan_and_analyze(token)
        return plan
    except Exception as e:
        logger.exception("Ecosystem scan failed for user %s: %s", user_id, e)
        return {"error": f"Scan failed: {str(e)}"}


@shared_task(bind=True, soft_time_limit=900, time_limit=1200)
def ecosystem_deploy_task(self, user_id: str, plan: dict) -> dict:
    """
    Deploy all services in the plan, in dependency order.

    This creates Service + Deployment records for each repo and triggers
    individual deployments via the existing smart_deploy_task.
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.models import Service, Deployment
    from apps.deployments.tasks import smart_deploy_task
    from apps.cloud.models import CloudProvider

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return {"error": "User not found"}

    services_plan = plan.get("services", [])
    if not services_plan:
        return {"error": "No services in deploy plan"}

    # Get default provider
    provider = CloudProvider.objects.first()
    if not provider:
        return {"error": "No cloud provider configured. Add one in Settings → Cloud Providers."}

    # Sort by deploy_order
    services_plan.sort(key=lambda s: s.get("deploy_order", 99))

    results = []
    created_services = {}  # name → Service obj for inter-service wiring

    for svc_plan in services_plan:
        if svc_plan.get("skip"):
            results.append({"repo": svc_plan["repo"], "status": "skipped"})
            continue

        repo = svc_plan["repo"]
        name = svc_plan.get("name", repo.split("/")[-1])
        port = svc_plan.get("port", 3000)
        stack = svc_plan.get("stack", "unknown")

        try:
            # Create or get the Service record
            service, created = Service.objects.get_or_create(
                name=name,
                owner=user,
                defaults={
                    "repository_url": f"https://github.com/{repo}",
                    "branch": "main",
                    "internal_port": port,
                }
            )
            if not created:
                # Update existing service
                service.repository_url = f"https://github.com/{repo}"
                service.branch = "main"
                service.internal_port = port
                service.save()

            created_services[name] = service

            # Process environment variables
            env_vars = svc_plan.get("env_vars", {})
            resolved_env = {}
            for key, value in env_vars.items():
                if value == "{{GENERATE}}":
                    resolved_env[key] = _generate_secret()
                elif value.startswith("{{SERVICE:") and value.endswith("}}"):
                    # Inter-service reference: {{SERVICE:repo-name}}
                    ref_name = value[10:-2]
                    ref_svc = created_services.get(ref_name)
                    if ref_svc:
                        resolved_env[key] = f"http://{ref_name}:{ref_svc.internal_port or 3000}"
                    else:
                        resolved_env[key] = f"http://{ref_name}:3000"
                elif value == "{{POSTGRES_URL}}":
                    resolved_env[key] = "postgresql://smsly:smsly@postgres:5432/smsly"
                elif value == "{{REDIS_URL}}":
                    resolved_env[key] = "redis://redis:6379/0"
                else:
                    resolved_env[key] = value

            # Save env vars to the service
            from apps.deployments.models import EnvVar
            for k, v in resolved_env.items():
                EnvVar.objects.update_or_create(
                    service=service,
                    key=k,
                    defaults={"value": v, "is_secret": "KEY" in k or "SECRET" in k or "PASSWORD" in k},
                )

            # Create deployment and trigger build
            deployment = Deployment.objects.create(
                service=service,
                commit_hash="ecosystem-deploy",
                commit_message=f"Zero-config ecosystem deploy ({stack})",
                status=Deployment.Status.QUEUED,
                build_logs=f"🚀 Ecosystem deploy: {repo} ({stack})\n"
                           f"Port: {port} | Build: {svc_plan.get('build', 'nixpacks')}\n"
                           f"Env vars: {len(resolved_env)} configured\n\n",
            )

            # Queue the actual deployment
            smart_deploy_task.delay(str(deployment.id), str(provider.id))

            results.append({
                "repo": repo,
                "name": name,
                "service_id": str(service.id),
                "deployment_id": str(deployment.id),
                "status": "queued",
                "stack": stack,
                "port": port,
            })

        except Exception as e:
            logger.error("Failed to deploy %s: %s", repo, e)
            results.append({
                "repo": repo,
                "name": name,
                "status": "failed",
                "error": str(e),
            })

    return {
        "status": "deploying",
        "total": len(services_plan),
        "queued": len([r for r in results if r["status"] == "queued"]),
        "skipped": len([r for r in results if r["status"] == "skipped"]),
        "failed": len([r for r in results if r["status"] == "failed"]),
        "services": results,
    }
