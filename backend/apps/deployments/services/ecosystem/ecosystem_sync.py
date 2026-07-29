import logging
import secrets

from .ecosystem_ai_analysis import analyze_ecosystem

logger = logging.getLogger(__name__)


def sync_ecosystem_envs(project_id: str) -> dict:
    """
    Exhaustive sync of all environment variables for a project ecosystem.
    Uses AI Senate to re-analyze every service in the project and push fresh linking/secrets.
    """
    from apps.deployments.models import EnvironmentVariable, Project, Service
    from django.db import transaction

    try:
        project = Project.objects.get(id=project_id)
        services = Service.objects.filter(project=project, status='ACTIVE')

        if not services.exists():
            return {"status": "error", "message": "No active services found in this project to sync."}

        # 1. Prepare data for AI analysis
        repos_data = []
        for s in services:
            repos_data.append({
                'repo': s.repository_url.split('github.com/')[-1] if s.repository_url else s.name,
                'name': s.name,
                'clone_dir': getattr(s, 'local_path', None), # Assume local path if available
                'stack': getattr(s, 'stack', 'unknown'),
                'description': getattr(s, 'description', '')
            })

        # 2. Trigger AI Ecosystem Analysis
        logger.info("Triggering AI Ecosystem Analysis for project %s (%d services)", project.name, len(services))
        plan = analyze_ecosystem(repos_data)

        if not plan or "services" not in plan:
            return {"status": "error", "message": "AI Senate failed to produce a valid ecosystem plan."}

        # 3. Persist the plan (Sync All)
        with transaction.atomic():
            for svc_plan in plan["services"]:
                svc_name = svc_plan.get("name")
                service = next((s for s in services if s.name == svc_name), None)
                if not service:
                    continue

                plan_envs = svc_plan.get("env_vars", {})
                for key, val in plan_envs.items():
                    # Placeholder resolution
                    final_val = val
                    if val == "{{GENERATE}}":
                        final_val = secrets.token_hex(32)
                    elif str(val).startswith("{{SERVICE:"):
                        # Keep placeholder for runtime resolution or resolve now if possible
                        target_repo = val.replace("{{SERVICE:", "").replace("}}", "")
                        target_svc = next((s for s in services if s.name == target_repo or (s.repository_url and target_repo in s.repository_url)), None)
                        if target_svc:
                            final_val = f"http://{target_svc.name}:{target_svc.internal_port}"

                    # Update or create
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=key,
                        defaults={
                            "value": final_val,
                            "is_secret": val == "{{GENERATE}}" or any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN", "PASSWORD"]),
                            "source": "SYSTEM"
                        }
                    )

        return {
            "status": "success",
            "message": f"Ecosystem sync complete for {len(services)} services. AI Provider: {plan.get('ai_provider', 'unknown')}",
            "plan": plan
        }

    except Exception as e:
        logger.exception("Failed to sync ecosystem envs: %s", e)
        return {"status": "error", "message": str(e)}
