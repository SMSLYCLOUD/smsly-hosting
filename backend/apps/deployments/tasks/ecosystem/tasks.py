import logging

logger = logging.getLogger(__name__)
import re
from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from apps.addons.services.addon_provisioner import addon_provisioner
from apps.deployments.constants import (
    RETRY_DELAY_FAST,
    RETRY_DELAY_HEAVY,
    RETRY_DELAY_STANDARD,
    TASK_TIME_LIMIT_DEPLOY,
)

from apps.cloud.models import CloudProvider
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.models.addons import Addon

from .constants import (
    _BUILD_DEFER_SECONDS,
    _DEFERRED_TASK_MAX_RETRIES,
    _MAX_CONCURRENT_BUILDS,
    _MAX_WAVE_RECHECKS,
    _MAX_WAVE_SIZE,
    _SECRET_HINTS,
)
from .helpers import (
    _addon_env_keys,
    _alias_ambiguity_report,
    _apply_service_profile,
    _build_dependency_waves,
    _cancel_all_remaining_deployments,
    _cancel_dependent_deployments,
    _cancel_unreleased_deployments,
    _canonical_repo_ref,
    _count_active_ecosystem_builds,
    _deployment_target_for_server,
    _detect_service_port,
    _ecosystem_project_name,
    _env_int,
    _extract_dependencies,
    _finalize_ecosystem_plan,
    _get_ecosystem_build_config,
    _has_enough_memory,
    _increment_active_ecosystem_builds,
    _inject_addon_env_defaults,
    _next_available_service_name,
    _normalize_env_vars,
    _order_key,
    _plan_addon_types,
    _queue_wave,
    _rebuild_ecosystem_build_counter,
    _repo_short_name,
    _repository_url,
    _resolve_dependency_map,
    _resolve_from_manifest_or_fallback,
    _runtime_watch_defaults,
    _select_shared_addon_anchor,
    _service_plan_addon_types,
    _slugify_name,
    _stack_runtime_defaults,
    _update_plan_progress,
    _validate_plan_structure,
    _validate_required_env,
    _validate_resolved_env,
    _wave_recheck_countdown,
)


@shared_task(bind=True, name="apps.deployments.tasks_ecosystem.ecosystem_scan_task", queue='deploy', soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0], time_limit=TASK_TIME_LIMIT_DEPLOY[1], max_retries=2, default_retry_delay=RETRY_DELAY_FAST, autoretry_for=(Exception,))
def ecosystem_scan_task(self, user_id: str, scan_window_days: int = 30, ai_provider: str | None = None, selected_repos: list | None = None, plan_id: str | None = None, project_id: str | None = None) -> dict:
    """
    Scan all of a user's GitHub repos and return a deploy plan.
    This is async because fetching and AI analysis can take 30-60s.

    scan_window_days filters repos by pushed_at recency (default 30 days).
    """
    from django.contrib.auth import get_user_model
    from apps.deployments.services.ecosystem import scan_and_analyze

    from apps.deployments.views.github import _get_github_token

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    token = _get_github_token(user)
    if not token:
        return {"error": "GitHub not connected. Please link your GitHub account first."}

    try:
        logger.info("Starting ecosystem scan for user %s with selected_repos: %s", user_id, selected_repos)

        # Persist initial progress so the frontend can show it on resume
        if plan_id:
            _update_plan_progress(plan_id, "Fetching and analyzing repositories...")

        from apps.deployments.models import Service
        existing_services = list(
            Service.objects.filter(owner=user)
            .values("name", "repository_url", "internal_port", "buildpack")
        )
        result = scan_and_analyze(token, ai_provider=ai_provider, selected_repos=selected_repos, existing_services=existing_services, scan_window_days=scan_window_days)
        logger.info(f"Ecosystem scan completed successfully for user {user_id}")

        if plan_id:
            from apps.deployments.models.ecosystem import EcosystemPlan
            try:
                plan_record = EcosystemPlan.objects.get(id=plan_id)
                plan_record.plan = result
                plan_record.scan_progress = "Scan complete!"
                plan_record.status = EcosystemPlan.Status.REVIEW
                plan_record.save(update_fields=['plan', 'scan_progress', 'status', 'updated_at'])
            except Exception as exc:
                logger.debug("Failed to save ecosystem plan result: %s", exc)

        return result
    except SoftTimeLimitExceeded:
        logger.warning("Ecosystem scan timed out for user %s", user_id, exc_info=True)
        return {
            "error": (
                "Ecosystem scan timed out before the full GitHub inventory finished. "
                "Retry the scan; large accounts may take several minutes."
            ),
            "code": "ecosystem_scan_timeout",
            "retryable": True,
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }
    except Exception as exc:
        logger.exception("Ecosystem scan failed unexpectedly for user %s: %s", user_id, exc)
        return {"error": f"Scan failed: {exc!s}"}


@shared_task(
    bind=True, name="apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task", queue='fast',
    soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0],
    time_limit=TASK_TIME_LIMIT_DEPLOY[1],
    max_retries=_DEFERRED_TASK_MAX_RETRIES,
    default_retry_delay=RETRY_DELAY_HEAVY,
    autoretry_for=(Exception,),
)
def ecosystem_deferred_build_task(self, deployment_id: str, provider_id: str, wave_index: int) -> dict:
    """Retry a deployment that was deferred due to concurrency limits."""
    deployment = Deployment.objects.filter(id=deployment_id).first()
    if not deployment:
        return {"status": "skipped", "reason": "deployment not found"}
    if deployment.status != Deployment.Status.QUEUED:
        return {"status": "skipped", "reason": f"status is {deployment.status}"}

    active = _count_active_ecosystem_builds()
    max_concurrent = _env_int("ECOSYSTEM_MAX_CONCURRENT_BUILDS", _MAX_CONCURRENT_BUILDS, minimum=1, maximum=10)

    if active >= max_concurrent:
        # Exponential backoff: base defer × retry count
        retry_count = getattr(self, 'request', {}).get('retries', 0)
        backoff = min(_BUILD_DEFER_SECONDS * (2 ** retry_count), 3600)
        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task",
            args=[deployment_id, provider_id, wave_index],
            countdown=backoff,
        )
        return {"status": "deferred", "active": active, "max": max_concurrent}

    _increment_active_ecosystem_builds()
    deployment.build_logs = (
        f"{deployment.build_logs or ''}"
        f"\n[Ecosystem] Deferred build slot acquired — dispatching.\n"
    )
    deployment.save(update_fields=["build_logs"])

    self.app.send_task(
        "apps.deployments.tasks.smart_deploy_task",
        args=[deployment_id, provider_id],
        kwargs={"skip_review": True},
    )
    return {"status": "dispatched", "deployment_id": deployment_id}


@shared_task(bind=True, name="apps.deployments.tasks_ecosystem.ecosystem_release_wave_task", queue='fast', soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0], time_limit=TASK_TIME_LIMIT_DEPLOY[1], max_retries=3, default_retry_delay=RETRY_DELAY_STANDARD, autoretry_for=(Exception,))
def ecosystem_release_wave_task(
    self,
    provider_id: str,
    waves: list[list[str]],
    wave_index: int = 1,
    recheck_count: int = 0,
    max_rechecks: int = _MAX_WAVE_RECHECKS,
    dependencies: dict[str, set[str]] | None = None,
    deployment_by_repo_key: dict[str, str] | None = None,
    cancel_others_on_failure: bool = False,
    plan_id: str | None = None,
) -> dict:
    """Release next wave, continuing successful branches and cancelling failed branches.

    When *cancel_others_on_failure* is ``True``, ANY failure in a wave causes
    ALL remaining queued deployments across all future waves to be cancelled
    (not just the ones that transitively depend on the failed node).
    """

    if dependencies:
        dependencies = {k: set(v) if isinstance(v, (set, tuple)) else v for k, v in dependencies.items()}

    # Rebuild build counter from actual deployment statuses to prevent drift
    _rebuild_ecosystem_build_counter()

    if not waves or wave_index > len(waves):
        _finalize_ecosystem_plan(plan_id, waves or [])
        return {"status": "completed", "waves": len(waves or [])}

    # Wave 0 has no previous wave to check — handle memory gating directly
    if wave_index == 0:
        if not _has_enough_memory():
            self.app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
                args=[provider_id, waves, 0, recheck_count, max_rechecks, dependencies, deployment_by_repo_key, cancel_others_on_failure],
                kwargs={"plan_id": plan_id},
                countdown=_wave_recheck_countdown(),
            )
            return {"status": "deferred", "wave": 0, "reason": "low_memory"}
        queued = _queue_wave(self.app, waves[0], provider_id, wave_index=0)
        if len(waves) >= 1:
            self.app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
                args=[provider_id, waves, 1, 0, max_rechecks, dependencies, deployment_by_repo_key, cancel_others_on_failure],
                kwargs={"plan_id": plan_id},
                countdown=_wave_recheck_countdown(),
            )
        return {"status": "released", "wave": 1, "queued": queued}

    previous_wave = [str(dep_id) for dep_id in waves[wave_index - 1]]
    deployments = list(Deployment.objects.filter(id__in=previous_wave).values("id", "status"))
    statuses = [dep["status"] for dep in deployments]

    if not statuses:
        # If the wave is missing entirely, we don't have enough context to continue branches
        return {
            "status": "blocked",
            "reason": "previous wave not found",
            "cancelled": 0,
        }

    failed_states = {
        Deployment.Status.FAILED,
        Deployment.Status.BUILD_FAILED,
        Deployment.Status.BACKUP_FAILED,
        Deployment.Status.MIGRATION_FAILED,
        Deployment.Status.CANCELLED,
    }
    in_progress_states = {
        Deployment.Status.QUEUED,
        Deployment.Status.REVIEW,
        Deployment.Status.BUILDING,
        Deployment.Status.AWAITING_APPROVAL,
        Deployment.Status.BACKUP_RUNNING,
        Deployment.Status.MIGRATION_PLANNING,
        Deployment.Status.MIGRATION_RUNNING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.HEALTH_CHECK,
        "STARTING",
    }

    # Bug 5 Fix: Retry failed deployments once before permanently failing them.
    for dep in deployments:
        if dep["status"] in failed_states and dep["status"] != Deployment.Status.CANCELLED:
            dep_obj = Deployment.objects.filter(id=dep["id"]).first()
            retry_count = int(getattr(dep_obj, "ecosystem_retry_count", 0) or 0) if dep_obj and isinstance(getattr(dep_obj, "ecosystem_retry_count", 0), (int, float)) else 1
            if dep_obj and retry_count < 1:
                # Mark as queued to retry once
                dep_obj.status = Deployment.Status.QUEUED
                dep_obj.ecosystem_retry_count = (dep_obj.ecosystem_retry_count or 0) + 1
                dep_obj.build_logs = (dep_obj.build_logs or "") + "\n[Ecosystem] Retrying (attempt 2/2)...\n"
                dep_obj.save(update_fields=["status", "ecosystem_retry_count", "build_logs"])

                # Re-queue the individual task
                self.app.send_task(
                    "apps.deployments.tasks.smart_deploy_task",
                    args=[str(dep_obj.id), provider_id],
                    kwargs={"skip_review": True},
                )
                # Update local list to consider this in-progress instead of failed
                dep["status"] = Deployment.Status.QUEUED

    # Re-evaluate statuses after possible retries
    statuses = [dep["status"] for dep in deployments]
    failed_ids = [str(dep["id"]) for dep in deployments if dep["status"] in failed_states]
    in_progress = any(status in in_progress_states for status in statuses)

    if in_progress:
        if recheck_count >= max_rechecks:
            # Time out waiting for remaining ones
            failed_ids.extend([str(dep["id"]) for dep in deployments if dep["status"] in in_progress_states])
            if cancel_others_on_failure and deployment_by_repo_key:
                cancelled = _cancel_all_remaining_deployments(
                    waves,
                    from_wave_index=wave_index,
                    failed_deployment_ids=failed_ids,
                    deployment_by_repo_key=deployment_by_repo_key,
                    reason="previous wave timed out and cancel-others-on-failure is enabled",
                )
            else:
                cancelled = 0
            cancelled += _cancel_unreleased_deployments(waves, wave_index, "ecosystem wave timed out")
            _finalize_ecosystem_plan(plan_id, waves)
            return {
                "status": "timed_out",
                "wave": wave_index,
                "cancelled": cancelled,
            }

        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
            args=[provider_id, waves, wave_index, recheck_count + 1, max_rechecks, dependencies, deployment_by_repo_key, cancel_others_on_failure],
            kwargs={"plan_id": plan_id},
            countdown=_wave_recheck_countdown(),
        )
        return {
            "status": "waiting",
            "wave": wave_index,
            "recheck_count": recheck_count + 1,
        }

    # At this point, everything is either terminal (ACTIVE or FAILED/CANCELLED)
    cancelled = 0
    if failed_ids:
        if cancel_others_on_failure and deployment_by_repo_key:
            cancelled = _cancel_all_remaining_deployments(
                waves,
                from_wave_index=wave_index,
                failed_deployment_ids=failed_ids,
                deployment_by_repo_key=deployment_by_repo_key,
                reason="a service deployment failed and cancel-others-on-failure is enabled",
            )

    if wave_index == len(waves):
        _finalize_ecosystem_plan(plan_id, waves)
        return {
            "status": "completed",
            "waves": len(waves),
            "cancelled_dependents": cancelled,
        }

    # Memory-aware gating: defer wave if system is under memory pressure
    if not _has_enough_memory():
        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
            args=[provider_id, waves, wave_index, 0, max_rechecks, dependencies, deployment_by_repo_key, cancel_others_on_failure],
            kwargs={"plan_id": plan_id},
            countdown=_wave_recheck_countdown(),
        )
        return {"status": "deferred", "wave": wave_index, "reason": "low_memory"}

    # We queue the next wave (which ignores CANCELLED statuses so only viable nodes deploy)
    queued = _queue_wave(self.app, waves[wave_index], provider_id, wave_index)
    if wave_index + 1 <= len(waves):
        self.app.send_task(
            "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
            args=[provider_id, waves, wave_index + 1, 0, max_rechecks, dependencies, deployment_by_repo_key, cancel_others_on_failure],
            kwargs={"plan_id": plan_id},
            countdown=_wave_recheck_countdown(),
        )
    return {
        "status": "released",
        "wave": wave_index + 1,
        "queued": queued,
        "cancelled_dependents": cancelled,
    }


@shared_task(
    bind=True, name="apps.deployments.tasks_ecosystem.ecosystem_deploy_task", queue='deploy',
    soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0], time_limit=TASK_TIME_LIMIT_DEPLOY[1],
    max_retries=3, default_retry_delay=RETRY_DELAY_STANDARD,
    autoretry_for=(Exception,),
)
def ecosystem_deploy_task(self, user_id: str, plan: dict, plan_id: str | None = None, project_id: str | None = None) -> dict:
    """
    Deploy all services in the plan using dependency-aware waves.

    SEC-ZT-007: Plan structure is validated against schema before any records
    are created. Secrets are encrypted using task_encryption before passing
    to Celery broker.

    This creates Service + Deployment records for each repo and triggers
    smart_deploy_task with skip_review=True as each wave becomes eligible.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    try:
        user = user_model.objects.get(id=user_id)
    except user_model.DoesNotExist:
        return {"error": "User not found"}

    if not isinstance(plan, dict):
        return {"error": "Invalid plan payload"}

    # SEC-ZT-007: Validate plan structure before creating any records
    schema_errors = _validate_plan_structure(plan)
    if schema_errors:
        logger.error("Plan schema validation failed: %s", schema_errors)
        return {
            "error": "Plan validation failed",
            "details": schema_errors,
        }

    services_plan = plan.get("services", [])
    use_shared_addons = plan.get("use_shared_addons", True)
    cancel_on_failure = plan.get("cancel_others_on_failure", False)
    shared_addon_config: dict[str, dict] = plan.get("shared_addon_config", {})
    if not isinstance(services_plan, list) or not services_plan:
        return {"error": "No services in deploy plan"}

    provider = CloudProvider.objects.filter(is_active=True).first() or CloudProvider.objects.first()
    if not provider:
        return {"error": "No cloud provider configured. Add one in Settings -> Cloud Providers."}

    # Track created resources for potential rollback
    _rollback_services: list[str] = []
    _rollback_deployments: list[str] = []
    _rollback_env_vars: list[str] = []
    _rollback_addons: list[str] = []

    build_cfg = _get_ecosystem_build_config()
    requested_wave_size = plan.get("wave_size", build_cfg["wave_size"])
    try:
        requested_wave_size = int(requested_wave_size)
    except (TypeError, ValueError):
        requested_wave_size = build_cfg["wave_size"]
    wave_size = max(1, min(_MAX_WAVE_SIZE, requested_wave_size))

    # Resolve project for scoping all created services
    project = None
    if project_id:
        from apps.deployments.models.core import Project
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            pass  # project_id is advisory — services will be created without project

    if not project and plan_id:
        from apps.deployments.models.ecosystem import EcosystemPlan
        try:
            _plan_rec = EcosystemPlan.objects.filter(id=plan_id, user=user).first()
            if _plan_rec and _plan_rec.project:
                project = _plan_rec.project
        except Exception as exc:
            logger.debug("Failed to look up ecosystem plan project: %s", exc)

    if not project:
        from apps.deployments.models.core import Project
        raw_name = str(
            plan.get("project_name")
            or plan.get("name")
            or (services_plan[0].get("repo", "").split("/")[-1] if services_plan and services_plan[0].get("repo") else "")
            or "Ecosystem Cluster"
        ).strip()
        if not raw_name:
            raw_name = "Ecosystem Cluster"
        proj_name = _ecosystem_project_name(raw_name)[:100]
        project = Project.objects.create(
            owner=user,
            name=proj_name,
            description="Auto-created by zero-config ecosystem deployment.",
            is_ephemeral=True,
        )
        logger.info("Auto-created ecosystem project '%s' (%s)", project.name, project.id)

    if plan_id:
        from apps.deployments.models.ecosystem import EcosystemPlan
        try:
            _plan_rec = EcosystemPlan.objects.filter(id=plan_id, user=user).first()
            if project and not _plan_rec.project:
                _plan_rec.project = project
                _plan_rec.save(update_fields=["project", "updated_at"])
        except Exception as exc:
            logger.debug("Failed to link ecosystem plan to project: %s", exc)

    # Ensure the ecosystem project always has its own ScopedRegistry.
    # Use .env credentials (which match the registry container's htpasswd) and
    # verify with docker login before saving. PlatformConfig DB values may be
    # stale if signals failed to sync htpasswd.
    if project:
        try:
            import subprocess

            from django.conf import settings
            from django.contrib.contenttypes.models import ContentType

            from apps.deployments.models.core import PlatformConfig
            from apps.deployments.models.registry_scope import ScopedRegistry

            _ct = ContentType.objects.get_for_model(Project)
            _has_registry = ScopedRegistry.objects.filter(
                content_type=_ct, object_id=project.id,
            ).exists()
            if not _has_registry:
                # Prefer .env credentials (source of truth for htpasswd),
                # fall back to PlatformConfig DB if .env is empty.
                _reg_user = getattr(settings, 'REGISTRY_USER', '') or PlatformConfig.get_config_value("registry_user") or "smsly-registry"
                _reg_pass = getattr(settings, 'REGISTRY_PASSWORD', '') or PlatformConfig.get_config_value("registry_password") or ""
                _reg_url = getattr(settings, 'CONTAINER_REGISTRY_URL', '') or PlatformConfig.get_config_value("container_registry_url") or "registry:5000"

                # Auto-generate a registry password if none is configured anywhere.
                # This ensures ecosystem projects always have valid credentials
                # for the platform's own internal registry.
                if not _reg_pass:
                    import secrets
                    _reg_pass = secrets.token_urlsafe(18)
                    logger.info(
                        "Auto-generated registry password for ecosystem project %s "
                        "(no REGISTRY_PASSWORD was configured)",
                        project.id,
                    )
                    try:
                        _cfg = PlatformConfig.load()
                        _cfg.registry_password = _reg_pass
                        _cfg.save(update_fields=['registry_password'])
                        logger.info("Persisted auto-generated registry password to PlatformConfig")
                    except Exception as _pw_exc:
                        logger.warning(
                            "Could not persist auto-generated registry password "
                            "to PlatformConfig: %s. Password is set on ScopedRegistry "
                            "but htpasswd may need manual sync.",
                            _pw_exc,
                        )

                # Verify credentials with docker login before saving
                _login_ok = False
                if _reg_user and _reg_pass:
                    try:
                        _login_proc = subprocess.run(
                            ['docker', 'login', _reg_url, '-u', _reg_user, '--password-stdin'],
                            input=_reg_pass, capture_output=True, text=True, timeout=15,
                        )
                        _login_ok = _login_proc.returncode == 0
                        if _login_ok:
                            logger.info(
                                "Registry login verified for %s (user=%s) — creating scoped registry",
                                _reg_url, _reg_user,
                            )
                        else:
                            logger.warning(
                                "Registry login failed for %s (user=%s, exit=%d). "
                                "ScopedRegistry will be created with .env credentials anyway — "
                                "push may fail if htpasswd is out of sync.",
                                _reg_url, _reg_user, _login_proc.returncode,
                            )
                    except Exception as login_exc:
                        logger.warning("Registry login check errored: %s", login_exc)

                ScopedRegistry.objects.create(
                    content_type=_ct,
                    object_id=project.id,
                    username=_reg_user,
                    password=_reg_pass,
                    is_internal=True,
                    is_active=True,
                )
                logger.info(
                    "Auto-created scoped registry for ecosystem project %s "
                    "(user=%s, url=%s, login_verified=%s)",
                    project.id, _reg_user, _reg_url, _login_ok,
                )
        except Exception as exc:
            logger.warning("Failed to ensure scoped registry for ecosystem project: %s", exc)

    # 1. Parse and validate manifest if provided, bulk verify env before continuing
    manifest_content = plan.get("manifest")
    if manifest_content:
        from apps.deployments.services.ecosystem_env import EcosystemEnvResolver
        from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
        from apps.deployments.services.ecosystem_persist import (
            bulk_persist_and_verify_ecosystem_env,
        )
        try:
            graph = build_ecosystem_graph(manifest_content)
            resolver = EcosystemEnvResolver(graph)
            success, _, errors = resolver.validate_and_resolve()
            if not success:
                return {"error": "Environment validation failed", "details": errors}
        except Exception as e:
            return {"error": f"Invalid manifest: {e}"}

    entries_by_key: dict[str, dict[str, Any]] = {}
    for svc_plan in services_plan:
        if not isinstance(svc_plan, dict):
            continue
        if svc_plan.get("skip"):
            continue

        repo = _canonical_repo_ref(svc_plan.get("repo"))
        if not repo:
            continue
        repo_key = repo.lower()

        source_name = str(svc_plan.get("name") or _repo_short_name(repo)).strip()
        requested_name = _slugify_name(source_name)
        entry = {
            "repo": repo,
            "repo_key": repo_key,
            "name": source_name,
            "requested_name": requested_name,
            "stack": str(svc_plan.get("stack") or "unknown"),
            "build": str(svc_plan.get("build") or "docker"),
            "deploy_order": _order_key(svc_plan),
            "depends_on": svc_plan.get("depends_on", []),
            "plan": svc_plan,
        }
        entries_by_key[repo_key] = entry

    if not entries_by_key:
        return {"error": "No deployable services in plan"}

    dependencies = _resolve_dependency_map(entries_by_key)
    waves_repo_keys, unresolved = _build_dependency_waves(
        entries_by_key=entries_by_key,
        dependencies=dependencies,
        wave_size=wave_size,
    )

    # SEC-ZT-007: Report alias ambiguity + unresolved cycles to user
    alias_warnings = _alias_ambiguity_report(dependencies, entries_by_key)
    if unresolved:
        alias_warnings.append(
            f"Unresolved/cyclic dependencies (deployed last): {', '.join(unresolved)}"
        )

    ordered_keys = [key for wave in waves_repo_keys for key in wave]
    results = []
    created_services: dict[str, Any] = {}
    shared_secrets: dict[str, str] = {}
    deployment_by_repo_key: dict[str, str] = {}

    # Bug 4 Fix: Provision required addons synchronously before wave 1.
    from apps.deployments.models import Service
    from apps.deployments.models.addons import Addon

    # Collect all needed addons across all services.
    required_addons = _plan_addon_types(plan.get("addons", []))
    for svc_plan in services_plan:
        if isinstance(svc_plan, dict) and not svc_plan.get("skip"):
            required_addons.update(_service_plan_addon_types(svc_plan, plan.get("addons", [])))

    created_service_records: list[Any] = []

    # Provision all required addons BEFORE service creation so env vars get real URLs.
    provisioned_addon_urls: dict[str, str] = {}
    addon_anchor_service = _select_shared_addon_anchor(created_service_records)

    if not addon_anchor_service and required_addons:
        # No existing services yet — create the anchor service early so we can
        # attach addons to it before the rest of the service loop runs.
        for svc_plan in services_plan:
            if not isinstance(svc_plan, dict) or svc_plan.get("skip"):
                continue
            repo = _canonical_repo_ref(svc_plan.get("repo"))
            if not repo:
                continue
            anchor_name = _slugify_name(svc_plan.get("name") or _repo_short_name(repo))
            anchor_branch = str(
                svc_plan.get("branch") or svc_plan.get("default_branch") or "main"
            ).strip() or "main"
            try:
                anchor_port = _detect_service_port(svc_plan, str(svc_plan.get("stack", "")))
            except (TypeError, ValueError):
                anchor_port = 3000

            existing_svc = Service.objects.filter(owner=user, name=anchor_name).first()
            if existing_svc:
                addon_anchor_service = existing_svc
                if project and addon_anchor_service.project != project:
                    addon_anchor_service.project = project
                    addon_anchor_service.save(update_fields=["project", "updated_at"])
            else:
                final_anchor_name = _next_available_service_name(Service, anchor_name)
                addon_anchor_service = Service.objects.create(
                    name=final_anchor_name,
                    owner=user,
                    project=project,
                    repository_url=_repository_url(repo),
                    branch=anchor_branch,
                    internal_port=anchor_port,
                    provider=provider,
                )
                _rollback_services.append(str(addon_anchor_service.id))
                _apply_service_profile(addon_anchor_service, {**svc_plan, "repo": repo}, provider, anchor_port)

            created_service_records.append(addon_anchor_service)
            aliases = {
                anchor_name, anchor_name.lower(),
                addon_anchor_service.name, addon_anchor_service.name.lower(),
                repo, repo.lower(),
                _repo_short_name(repo), _repo_short_name(repo).lower(),
            }
            for alias in aliases:
                created_services[alias] = addon_anchor_service
            break

    if not use_shared_addons:
        logger.info("Shared addons disabled — each service will provision its own addons independently")

    def _addon_is_shared(addon_type: str) -> bool:
        """Return True if this addon type should be provisioned once and shared.

        Per-addon ``shared_addon_config`` overrides the global ``use_shared_addons``
        flag.  If an addon type is not listed in ``shared_addon_config``, the global
        flag applies.
        """
        cfg = shared_addon_config.get(addon_type)
        if isinstance(cfg, dict):
            return bool(cfg.get("shared", use_shared_addons))
        return use_shared_addons

    if addon_anchor_service and required_addons:
        supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())
        for addon_type in required_addons:
            if addon_type not in supported_addons:
                logger.warning("Ecosystem addon %s is not supported; skipping", addon_type)
                continue

            # Per-addon sharing resolution: check shared_addon_config first,
            # then fall back to the global use_shared_addons flag.
            if not _addon_is_shared(addon_type):
                logger.info("Addon %s is configured as individual (not shared); skipping shared provisioning", addon_type)
                continue

            existing_addon = Addon.objects.filter(service=addon_anchor_service, addon_type=addon_type).first()
            if not existing_addon:
                existing_addon = Addon.objects.create(
                    service=addon_anchor_service,
                    name=f"{addon_type.lower()}-shared"[:255],
                    addon_type=addon_type,
                    status=Addon.Status.PROVISIONING,
                )
                _rollback_addons.append(str(existing_addon.id))

            try:
                logger.info("Provisioning shared addon %s for ecosystem", addon_type)
                _cid, url = addon_provisioner.provision(existing_addon)
                existing_addon.connection_url = url
                existing_addon.status = Addon.Status.ACTIVE
                existing_addon.save(update_fields=['connection_url', 'status', 'updated_at'])
                provisioned_addon_urls[addon_type] = url
                logger.info("Provisioned %s addon: %s", addon_type, existing_addon.id)
            except Exception as exc:
                logger.error("Failed to provision shared addon %s: %s", addon_type, exc)
                existing_addon.status = Addon.Status.FAILED
                existing_addon.save(update_fields=['status'])

    for repo_key in ordered_keys:
        entry = entries_by_key[repo_key]
        svc_plan = entry["plan"]
        repo = entry["repo"]
        requested_name = entry["requested_name"]
        stack = entry["stack"]
        build_method = entry["build"]

        try:
            port = _detect_service_port(svc_plan, stack)
        except (TypeError, ValueError):
            port = 3000
        port = max(1, min(65535, port))

        server_id = svc_plan.get("server_id") or plan.get("server_id")
        server = None
        if server_id:
            from apps.deployments.models import ManagedServer
            try:
                if str(server_id).lower() in ("local", "primary"):
                    server = ManagedServer.get_primary()
                else:
                    server = ManagedServer.objects.filter(id=server_id, owner=user).first()
            except Exception as exc:
                logger.debug("Failed to resolve deployment server %s: %s", server_id, exc)
        else:
            from apps.deployments.services.node_selector import select_eligible_node
            server = select_eligible_node(user)

        target_server, target_is_local = _deployment_target_for_server(server, provider)

        if not server and not target_is_local:
            # Mark the deployment as pending so the user sees a clear status
            # and can retry later when a node becomes available.
            logger.error(f"No eligible deployment node available for {repo}.")
            results.append({
                "repo": repo,
                "name": requested_name,
                "status": "pending",
                "error": "No eligible deployment node available."
            })
            # Optionally, create a placeholder Service with a pending flag
            # to surface in the UI. This avoids silent failures.
            try:
                Service.objects.create(
                    name=requested_name,
                    owner=user,
                    project=project,
                    repository_url=_repository_url(repo),
                    branch=str(
                        svc_plan.get("branch")
                        or svc_plan.get("default_branch")
                        or "main"
                    ).strip() or "main",
                    internal_port=port,
                    provider=provider,
                    server=None,
                    status=Service.Status.UNKNOWN,
                )
            except Exception:
                # If creation fails (e.g., model does not have a status field),
                # we simply continue; the pending entry in ``results`` is still
                # returned to the caller.
                pass
            continue

        try:
            service = Service.objects.filter(owner=user, name=requested_name).first()
            if service is None:
                final_name = _next_available_service_name(Service, requested_name)
                service = Service.objects.create(
                    name=final_name,
                    owner=user,
                    project=project,
                    repository_url=_repository_url(repo),
                    branch=str(
                        svc_plan.get("branch")
                        or svc_plan.get("default_branch")
                        or "main"
                    ).strip() or "main",
                    internal_port=port,
                    provider=provider,
                    server=server,
                )
                _rollback_services.append(str(service.id))
            elif project and service.project != project:
                service.project = project
                service.save(update_fields=["project", "updated_at"])

            service_profile = {**svc_plan, "repo": repo}
            if target_is_local and server is None:
                service_profile["force_local_target"] = True
            _apply_service_profile(service, service_profile, provider, port, server=server)

            if all(getattr(existing, "id", None) != getattr(service, "id", None) for existing in created_service_records):
                created_service_records.append(service)

            # Keep multiple aliases for inter-service references.
            aliases = {
                entry["name"],
                entry["name"].lower(),
                requested_name,
                requested_name.lower(),
                service.name,
                service.name.lower(),
                repo,
                repo.lower(),
                _repo_short_name(repo),
                _repo_short_name(repo).lower(),
            }
            for alias in aliases:
                created_services[alias] = service

            normalized_env = _normalize_env_vars(svc_plan.get("env_vars", {}))
            svc_plan["env_vars"] = normalized_env
            service_addon_types = _service_plan_addon_types(svc_plan, plan.get("addons", []))

            # When shared addons are disabled, or this specific addon is not shared,
            # provision each service's addons independently.
            service_addon_urls: dict[str, str] = {}
            if service_addon_types:
                supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())
                for addon_type in service_addon_types:
                    if addon_type not in supported_addons:
                        logger.warning("Ecosystem addon %s is not supported; skipping", addon_type)
                        continue
                    # Only provision individually if this addon is NOT shared
                    if _addon_is_shared(addon_type):
                        continue
                    try:
                        svc_addon = Addon.objects.create(
                            service=service,
                            name=f"{service.name}-{addon_type.lower()}"[:255],
                            addon_type=addon_type,
                            status=Addon.Status.PROVISIONING,
                        )
                        _rollback_addons.append(str(svc_addon.id))
                        _cid, url = addon_provisioner.provision(svc_addon)
                        svc_addon.connection_url = url
                        svc_addon.status = Addon.Status.ACTIVE
                        svc_addon.save(update_fields=['connection_url', 'status', 'updated_at'])
                        service_addon_urls[addon_type] = url
                        logger.info("Provisioned individual addon %s for service %s", addon_type, service.name)
                    except Exception as exc:
                        logger.error("Failed to provision individual addon %s for %s: %s", addon_type, service.name, exc)

            # ── Manifest-backed env resolution (replaces AI hallucination) ──
            # When the source repo is available locally, read actual .env.example
            # and SECRETS-MANIFEST.yaml files to produce a fully resolved,
            # grounded env configuration. Falls back to placeholder resolution
            # + AI Senate only when source files are unavailable.
            # Merge shared + individual addon URLs: prefer per-service URLs
            # (individual provisioning) and fall back to shared provisioned URLs.
            active_addon_urls: dict[str, str] = {**provisioned_addon_urls, **service_addon_urls}
            resolved_env = _resolve_from_manifest_or_fallback(
                repo=repo,
                service_name=service.name,
                entry=entry,
                svc_plan=svc_plan,
                created_services=created_services,
                shared_addons=active_addon_urls,
                shared_secrets=shared_secrets,
                stack=stack,
            )
            _inject_addon_env_defaults(resolved_env, service_addon_types, active_addon_urls)

            # Filter out Django/framework-specific vars from non-Django services.
            if stack not in {"django", "python"}:
                _DJANGO_ONLY_VARS = {
                    "ADMIN_EMAIL", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS",
                    "ALLOWED_HOSTS", "FERNET_KEY", "SECRET_KEY", "HOSTNAME",
                    "DJANGO_SETTINGS_MODULE", "DJANGO_SECRET_KEY", "CSRF_TRUSTED_ORIGINS",
                }
                for dv in _DJANGO_ONLY_VARS:
                    resolved_env.pop(dv, None)

            # Stack runtime defaults — PORT must override any AI-injected value.
            _stack_defs = _stack_runtime_defaults(stack, port)
            for key, value in _stack_defs.items():
                if key == "PORT":
                    resolved_env[key] = value  # Always override PORT
                else:
                    resolved_env.setdefault(key, value)
            for key, value in _runtime_watch_defaults(user).items():
                resolved_env.setdefault(key, value)

            _validate_resolved_env(resolved_env)

            # Ensure required production env vars are present
            _validate_required_env(resolved_env, service_addon_types)

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
                branch=service.branch or "",
                status=Deployment.Status.QUEUED,
                target_server=target_server,
                target_is_local=target_is_local,
                build_logs=(
                    f"Ecosystem deploy: {repo} ({stack})\n"
                    f"Port: {port} | Build strategy: {build_method}\n"
                    f"Env vars: {len(resolved_env)} configured\n"
                    f"Depends on: {', '.join(_extract_dependencies(entry['depends_on'])) or '(none)'}\n\n"
                ),
            )
            _rollback_deployments.append(str(deployment.id))

            deployment_by_repo_key[repo_key] = str(deployment.id)
            results.append({
                "repo": repo,
                "name": service.name,
                "server": service.server.name if service.server else "N/A",
                "service_id": str(service.id),
                "deployment_id": str(deployment.id),
                "status": "queued",
                "stack": stack,
                "port": port,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to prepare deploy for %s: %s", repo, exc)
            results.append({
                "repo": repo,
                "name": requested_name,
                "status": "failed",
                "error": str(exc),
            })

    # Bulk persist env if using a manifest
    if manifest_content:
        from apps.deployments.services.ecosystem_persist import (
            bulk_persist_and_verify_ecosystem_env,
        )
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_content, created_services)
        if not success:
            logger.error(f"Bulk persistence failed: {msg}")
            # Mark all deployments as failed
            for repo_key, dep_id in deployment_by_repo_key.items():
                Deployment.objects.filter(id=dep_id).update(
                    status=Deployment.Status.FAILED,
                    build_logs=f"Failed to persist valid environment variables: {msg}"
                )
            return {"error": f"Env validation failed: {msg}"}

    waves: list[list[str]] = []
    for wave in waves_repo_keys:
        deployment_ids = [
            deployment_by_repo_key[repo_key]
            for repo_key in wave
            if repo_key in deployment_by_repo_key
        ]
        if deployment_ids:
            waves.append(deployment_ids)

    # Reconcile: update ALL services' addon env vars with real provisioned URLs.
    # Only set env vars that are missing or still contain unresolved placeholders.
    # Do NOT overwrite values that were already resolved with embedded suffixes
    # (e.g. "{{POSTGRES_URL}}/identity" -> "postgres://.../identity").
    if provisioned_addon_urls:
        updated_service_ids: set = set()
        for repo_key, entry in entries_by_key.items():
            svc = created_services.get(repo_key)
            if not svc or svc.id in updated_service_ids:
                continue
            svc_addon_types = _service_plan_addon_types(entry.get("plan", {}), plan.get("addons", []))
            for addon_type, url in provisioned_addon_urls.items():
                if addon_type in svc_addon_types:
                    for env_key in _addon_env_keys(addon_type):
                        existing = EnvironmentVariable.objects.filter(
                            service=svc, key=env_key,
                        ).first()
                        if existing and existing.value and not re.search(r"\{\{.*?\}\}", existing.value):
                            # Already resolved (possibly with embedded suffix) — skip
                            continue
                        EnvironmentVariable.objects.update_or_create(
                            service=svc,
                            key=env_key,
                            defaults={"value": url, "is_secret": True},
                        )
                        logger.info("Reconciled %s %s with provisioned %s URL", svc.name, env_key, addon_type)
            updated_service_ids.add(svc.id)

    queued_now = 0
    # Pass dependencies to the wave task
    safe_dependencies = {k: list(v) for k, v in dependencies.items()} if dependencies else {}

    if waves:
        if not _has_enough_memory():
            # Defer first wave — start via release task with memory gating
            self.app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
                args=[str(provider.id), waves, 0, 0, _MAX_WAVE_RECHECKS, safe_dependencies, deployment_by_repo_key, cancel_on_failure],
                kwargs={"plan_id": plan_id},
                countdown=_wave_recheck_countdown(),
            )
        else:
            queued_now = _queue_wave(self.app, waves[0], str(provider.id), wave_index=0)
            if len(waves) >= 1:
                self.app.send_task(
                    "apps.deployments.tasks_ecosystem.ecosystem_release_wave_task",
                    args=[str(provider.id), waves, 1, 0, _MAX_WAVE_RECHECKS, safe_dependencies, deployment_by_repo_key, cancel_on_failure],
                    kwargs={"plan_id": plan_id},
                    countdown=_wave_recheck_countdown(),
                )

    deploy_result = {
        "status": "deploying",
        "total": len(services_plan),
        "prepared": len(results),
        "queued_immediately": queued_now,
        "waves": len(waves),
        "wave_size": wave_size,
        "unresolved_dependency_nodes": unresolved,
        "alias_warnings": alias_warnings,
        "queued": len([r for r in results if r["status"] == "queued"]),
        "skipped": len([s for s in services_plan if isinstance(s, dict) and s.get("skip")]),
        "failed": len([r for r in results if r["status"] == "failed"]),
        "services": results,
    }

    try:
        if plan_id:
            from apps.deployments.models.ecosystem import EcosystemPlan
            plan_record = EcosystemPlan.objects.filter(id=plan_id, user=user).first()
            if plan_record:
                plan_record.services_created = results
                if deploy_result.get("failed", 0) == len(results):
                    plan_record.status = EcosystemPlan.Status.FAILED
                    plan_record.error_message = "All services failed to deploy"
                else:
                    plan_record.status = EcosystemPlan.Status.DEPLOYING
                plan_record.save(update_fields=['services_created', 'status', 'error_message', 'updated_at'])
    except Exception as exc:
        logger.debug("Failed to update ecosystem plan deployment status: %s", exc)

    return deploy_result


def _rollback_ecosystem_deploy(
    service_ids: list[str],
    deployment_ids: list[str],
    addon_ids: list[str],
    env_var_keys: list[str],
):
    """
    SEC-ZT-007: Clean up partially created resources on deploy failure.
    Removes services, deployments, addons, and env vars created during
    the failed ecosystem deployment attempt.
    """

    logger.warning("Rolling back ecosystem deploy: %d services, %d deployments, %d addons",
                   len(service_ids), len(deployment_ids), len(addon_ids))

    if deployment_ids:
        Deployment.objects.filter(id__in=deployment_ids).exclude(
            status__in=("ACTIVE", "BUILDING"),
        ).delete()

    if addon_ids:
        Addon.objects.filter(id__in=addon_ids).exclude(
            status="ACTIVE",
        ).delete()

    if env_var_keys:
        EnvironmentVariable.objects.filter(
            service_id__in=service_ids,
            key__in=env_var_keys,
        ).delete()

    if service_ids:
        Service.objects.filter(id__in=service_ids).delete()

    _rebuild_ecosystem_build_counter()
    logger.info("Rollback complete")
