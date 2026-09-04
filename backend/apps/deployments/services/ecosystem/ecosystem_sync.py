import logging
import re
import secrets

from apps.cloud.services.build_constants import is_secret_env_var
from .ecosystem_ai_analysis import analyze_ecosystem

logger = logging.getLogger(__name__)


def _addon_type_from_token(token: str) -> str:
    """Map an {{ADDON_URL}}-style placeholder token back to an addon type."""
    token = str(token or "").strip().upper()
    if token in {"DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "PG_URL"}:
        return "POSTGRES"
    if token in {"CACHE_URL", "REDIS_URL", "REDIS_URI"}:
        return "REDIS"
    if token.endswith("_URL") or token.endswith("_URI"):
        return token[:-4]
    return ""


def sync_ecosystem_envs(project_id: str) -> dict:
    """
    Exhaustive sync of all environment variables for a project ecosystem.
    Uses AI Senate to re-analyze every service in the project and push fresh linking/secrets.
    """
    from apps.deployments.models import Addon, EnvironmentVariable, Project, Service

    try:
        project = Project.objects.get(id=project_id)
        services = list(Service.objects.filter(project=project, status='ACTIVE'))

        if not services:
            return {"status": "error", "message": "No active services found in this project to sync."}

        # 1. Prepare data for AI analysis
        repos_data = []
        for s in services:
            repos_data.append({
                'repo': s.repository_url.split('github.com/')[-1] if s.repository_url else s.name,
                'name': s.name,
                'stack': getattr(s, 'buildpack', '') or 'unknown',
                'internal_port': s.internal_port,
                'description': ''
            })

        # 2. Trigger AI Ecosystem Analysis
        logger.info("Triggering AI Ecosystem Analysis for project %s (%d services)", project.name, len(services))
        plan = analyze_ecosystem(repos_data)

        if not plan or "services" not in plan:
            return {"status": "error", "message": "AI Senate failed to produce a valid ecosystem plan."}

        # Resolve addon placeholders from existing ACTIVE addons in this project.
        addon_urls: dict[str, str] = {}
        for addon in Addon.objects.filter(
            service__project=project,
            status=Addon.Status.ACTIVE,
        ).exclude(connection_url=''):
            addon_urls.setdefault(addon.addon_type, addon.connection_url)

        # Shared secrets stay stable within (and across) sync runs.
        run_shared_secrets: dict[str, str] = {}

        def _sub_placeholder(match: re.Match) -> str:
            token = match.group(1).strip()
            upper = token.upper()
            if upper.startswith("SHARED_SECRET:"):
                name = token[14:].strip().lower() or "shared"
                return run_shared_secrets.setdefault(name, secrets.token_hex(32))
            if upper.startswith("SERVICE:"):
                target = token[8:].strip()
                target_svc = next(
                    (s for s in services if s.name == target
                     or (s.repository_url and target in s.repository_url)),
                    None,
                )
                if target_svc:
                    return f"http://{target_svc.name}:{target_svc.internal_port}"
                return match.group(0)
            addon_type = _addon_type_from_token(token)
            if addon_type and addon_type in addon_urls:
                return addon_urls[addon_type]
            return match.group(0)

        def _resolve_plan_value(raw_val) -> str | None:
            """Resolve placeholders to concrete values.

            Returns None when placeholders remain unresolved — the caller must
            skip persisting instead of writing the literal string into the DB.
            """
            text = str(raw_val or "")
            if text == "{{GENERATE}}":
                return secrets.token_hex(32)
            resolved = re.sub(r"\{\{(.+?)\}\}", _sub_placeholder, text)
            if re.search(r"\{\{.*?\}\}", resolved):
                return None
            return resolved

        # 3. Persist the plan (Sync All)
        from django.db import transaction

        with transaction.atomic():
            for svc_plan in plan["services"]:
                svc_name = svc_plan.get("name")
                service = next((s for s in services if s.name == svc_name), None)
                if not service:
                    continue

                plan_envs = svc_plan.get("env_vars", {})
                for key, val in plan_envs.items():
                    key_upper = str(key or "").strip().upper()
                    if not key_upper:
                        continue
                    final_val = _resolve_plan_value(val)
                    if final_val is None:
                        logger.warning(
                            "Sync: skipping %s for %s — unresolved placeholder(s)",
                            key_upper, service.name,
                        )
                        continue
                    if not final_val.strip():
                        # Never persist an empty override — it would mask the
                        # app's own default value.
                        continue

                    # LOCKED vars are never overridden by platform
                    # auto-injection — the user explicitly pinned them.
                    # Skip instead of stomping with fresh AI Senate output.
                    if EnvironmentVariable.objects.filter(
                        service=service, key=key_upper, is_locked=True,
                    ).exists():
                        logger.debug(
                            "Sync: skipping locked %s for %s — user-pinned value",
                            key_upper, service.name,
                        )
                        continue

                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=key_upper,
                        defaults={
                            "value": final_val,
                            "is_secret": is_secret_env_var(key_upper),
                            "source": "SYSTEM"
                        }
                    )

        # Persist shared secrets generated this run so future syncs/deploys reuse them.
        if run_shared_secrets:
            try:
                from apps.deployments.models.ecosystem import EcosystemSharedSecret
                owner = project.owner
                for name, value in run_shared_secrets.items():
                    EcosystemSharedSecret.objects.update_or_create(
                        user=owner, name=name, defaults={"value": value},
                    )
            except Exception as exc:
                logger.warning("Failed to persist synced shared secrets: %s", exc)

        return {
            "status": "success",
            "message": f"Ecosystem sync complete for {len(services)} services. AI Provider: {plan.get('ai_provider', 'unknown')}",
            "plan": plan
        }

    except Exception as e:
        logger.exception("Failed to sync ecosystem envs: %s", e)
        return {"status": "error", "message": str(e)}
