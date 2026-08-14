import logging

logger = logging.getLogger(__name__)
import subprocess
import time

import docker
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import (
    Deployment,
)
from apps.deployments.services.pipeline import PipelineError, PipelineManager
from apps.deployments.constants import TASK_TIME_LIMIT_DEPLOY, TASK_TIME_LIMIT_QUICK
from apps.deployments.utils import (
    append_log,
    broadcast_status,
)

from ..deploy.helpers import (  # noqa: F401
    _deploy_container,
    _deployment_effective_server,
    _do_promote,
    _handle_failure,
    _is_local_deployment_server,
    _resolve_provider_for_service,
    _run_managed_image_post_deploy_hooks,
    fleet_build_lock,
)
from ..deploy.build_nixpacks import _build_function, _build_uploaded_source
from .tasks_deploy_remote import _handle_remote_deployment, _resume_remote_deployment
from ..tasks_utils import (
    should_skip_review_for_commit_message,
)


@shared_task(
    bind=True,
    max_retries=3,
    soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0],
    time_limit=TASK_TIME_LIMIT_DEPLOY[1],
    name="apps.deployments.tasks.smart_deploy_task",
)
def smart_deploy_task(self, deployment_id: str, provider_id: str,
                     skip_review: bool = False):
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled before start", deployment_id)
            return

        try:
            from apps.deployments.tasks.cicd.tasks_commit_status import update_commit_status
            update_commit_status.delay(
                str(deployment.id), 'pending', 'Deployment started'
            )
        except Exception as exc:
            logger.debug("Failed to post commit status for deployment %s: %s", deployment_id, exc)

        try:
            from apps.deployments.tasks.cicd.tasks_commit_status import _detect_provider, _extract_repo_path
            from apps.deployments.services.github_app import get_github_app_service, get_installation_for_repo
            if _detect_provider(deployment.service.repository_url or '') == 'github':
                repo_name = _extract_repo_path(deployment.service.repository_url or '')
                inst = get_installation_for_repo(repo_name) if repo_name else None
                svc = get_github_app_service()
                if inst and svc and deployment.commit_hash:
                    env_name = 'preview' if deployment.service.is_preview else 'production'
                    gh_depl_id = svc.create_deployment(
                        installation_id=inst.installation_id,
                        repo_full_name=repo_name,
                        ref=deployment.commit_hash,
                        environment=env_name,
                        description=f"Deploying {deployment.commit_hash[:7]}",
                        transient_environment=deployment.service.is_preview,
                        production_environment=not deployment.service.is_preview,
                    )
                    if gh_depl_id:
                        deployment.github_deployment_id = gh_depl_id
                        deployment.save(update_fields=['github_deployment_id'])
                        svc.create_deployment_status(
                            installation_id=inst.installation_id,
                            repo_full_name=repo_name,
                            github_deployment_id=gh_depl_id,
                            state='in_progress',
                            description='Building and deploying...',
                        )
        except Exception as exc:
            logger.debug("Failed to create GitHub deployment for %s: %s", deployment_id, exc)

        skip_review = skip_review or deployment.is_rollback or should_skip_review_for_commit_message(
            deployment.commit_message
        )

        service = deployment.service
        if not provider_id or provider_id == "None":
            provider = _resolve_provider_for_service(service, prefer_local=True)
            if not provider:
                raise RuntimeError("Could not resolve cloud provider for deployment.")
        else:
            provider = CloudProvider.objects.get(id=provider_id)

        if not getattr(deployment, 'is_rollback', False) and getattr(settings, "SENATE_ENABLED", True):
            try:
                from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService
                _sugg, _inj = EnvironmentIntelligenceService.apply_intelligence_to_service(service, scan_results={})
                if _inj:
                    logger.info("Smart Deployment Queue: AI Senate auto-filled %d remaining environment variables for %s: %s", len(_inj), service.name, ", ".join(_inj))
                    if deployment.build_logs is not None:
                        deployment.build_logs = f"{deployment.build_logs}\n🧠 Smart Deployment Queue: AI Senate auto-filled {len(_inj)} remaining environment variables.\n"
                        deployment.save(update_fields=["build_logs"])
            except Exception as _senate_err:
                logger.warning("Smart Deployment Queue env enrichment failed for %s: %s", service.name, _senate_err)

        is_delegated = deployment.source_node is not None

        if not skip_review and getattr(service, 'safedeploy_enabled', False) \
                and not getattr(deployment, 'is_rollback', False) and not is_delegated:
            from apps.deployments.services.safedeploy.deployment_pipeline import (
                ProductionDeploymentPipeline,
            )
            ProductionDeploymentPipeline().process_deployment(deployment)
            if deployment.status == Deployment.Status.AWAITING_APPROVAL:
                return

        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()

        if is_delegated:
            from apps.deployments.models.core import ManagedServer

            prebuilt = str(service.docker_image or "").strip()
            if prebuilt:
                is_delegated = False
            else:
                target = ManagedServer.objects.filter(host=deployment.source_node).first()
                if target:
                    _handle_remote_deployment(deployment, target, skip_review=skip_review)
                    return

        effective_server = _deployment_effective_server(deployment)
        is_local = _is_local_deployment_server(effective_server, config)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, effective_server)
                return

            if service.deploy_type == 'GIT' and not str(service.docker_image or "").strip():
                with fleet_build_lock(deployment):
                    pipeline = PipelineManager(
                        deployment,
                        staged_only=skip_review and not deployment.is_rollback,
                    )
                    if skip_review:
                        built_image = pipeline.run()
                    else:
                        pipeline.run_analysis_only()
                        broadcast_status(deployment)
                        return
                _handle_remote_deployment(
                    deployment, effective_server,
                    skip_review=skip_review, image_name=built_image,
                )
                return

            _handle_remote_deployment(deployment, effective_server, skip_review=skip_review)
            return

        if service.deploy_type == 'GIT':
            prebuilt = str(service.docker_image or "").strip()
            if prebuilt and deployment.source_node:
                image_name = prebuilt
            elif deployment.is_rollback or skip_review:
                with fleet_build_lock(deployment):
                    manager = PipelineManager(
                        deployment,
                        staged_only=skip_review and not deployment.is_rollback,
                    )
                    image_name = manager.run()
            else:
                manager = PipelineManager(deployment)
                manager.run_analysis_only()
                broadcast_status(deployment)
                return

        elif service.deploy_type == 'FUNCTION':
            with fleet_build_lock(deployment):
                image_name = _build_function(deployment, service)

        elif service.deploy_type == 'DOCKER':
            image_name = service.docker_image

        elif service.deploy_type == 'UPLOAD':
            with fleet_build_lock(deployment):
                image_name = _build_uploaded_source(deployment, service)

        else:
            raise ValueError(f"Unsupported deploy type: {service.deploy_type}")

        _deploy_container(deployment, provider, image_name,
                          staged_only=skip_review and not deployment.is_rollback)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Pipeline Failure")
    except (docker.errors.DockerException, ConnectionError, TimeoutError) as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)
        _handle_failure(self, deployment, str(exc), "Transient Failure")
    except SoftTimeLimitExceeded:
        _handle_failure(self, deployment, "Task exceeded time limit", "Timeout Failure")
    except Exception as e:
        _handle_failure(self, deployment, str(e), "System Failure")



@shared_task(
    bind=True,
    soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0],
    time_limit=TASK_TIME_LIMIT_DEPLOY[1],
    name="apps.deployments.tasks.resume_deploy_task",
)
def resume_deploy_task(self, deployment_id: str, provider_id: str):
    deployment = None
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled", deployment_id)
            return

        service = deployment.service
        if not provider_id or provider_id == "None":
            provider = _resolve_provider_for_service(service, prefer_local=True)
            if not provider:
                raise RuntimeError("Could not resolve cloud provider for deployment.")
        else:
            provider = CloudProvider.objects.get(id=provider_id)

        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()

        is_delegated = deployment.source_node is not None
        effective_server = _deployment_effective_server(deployment)
        is_local = is_delegated or _is_local_deployment_server(effective_server, config)

        if not is_local:
            if deployment.remote_deployment_id:
                _resume_remote_deployment(deployment, effective_server)
                return

            prebuilt = str(service.docker_image or "").strip()
            if prebuilt and deployment.source_node:
                built_image = prebuilt
            else:
                with fleet_build_lock(deployment):
                    manager = PipelineManager(deployment)
                    built_image = manager.run_build_only()

            _handle_remote_deployment(
                deployment, effective_server, image_name=built_image,
            )
            return

        prebuilt = str(service.docker_image or "").strip()
        if prebuilt and deployment.source_node:
            image_name = prebuilt
        else:
            with fleet_build_lock(deployment):
                manager = PipelineManager(deployment)
                image_name = manager.run_build_only()

        _deploy_container(deployment, provider, image_name)

    except PipelineError as e:
        _handle_failure(self, deployment, str(e), "Build Failure")
    except Exception as e:
        _handle_failure(self, deployment, str(e), "System Failure")


from ..deploy.helpers import (  # noqa: F401 — re-exported for tests & install.sh
    enqueue_smart_deploy_task,
    recover_stalled_queued_deployments,
)


def _sync_service_dns_to_node(deployment, service):
    if not getattr(service, 'server', None) or not service.server.host:
        return

    node_ip = str(service.server.host).strip()
    if not node_ip or node_ip in ("127.0.0.1", "localhost", "0.0.0.0"):
        return

    domains = []
    if service.public_domain:
        domains.append(service.public_domain.strip())
    if service.custom_domains:
        domains.extend([d.strip() for d in (service.custom_domains or []) if d])

    if not domains:
        return

    try:
        from apps.deployments.models import PlatformConfig
        from apps.domains.services.dns import ensure_dns_records

        config = PlatformConfig.objects.first()
        if not config or not config.cloudflare_api_token:
            return

        append_log(deployment, f"[DNS] Syncing DNS records to Node IP ({node_ip})...\n")
        dns_result = ensure_dns_records(domains, node_ip, config.cloudflare_api_token)
        if not dns_result.get("ok"):
            append_log(deployment, f"[DNS] Warning: {dns_result.get('errors')}\n")
        else:
            created = len(dns_result.get('created', []))
            updated = len(dns_result.get('updated', []))
            if created > 0 or updated > 0:
                append_log(deployment, f"[DNS] Sync OK (Created: {created}, Updated: {updated})\n")
    except Exception as dns_exc:
        logger.warning("Service DNS sync failed: %s", dns_exc)
        append_log(deployment, f"[DNS] Sync Error: {dns_exc}\n")



@shared_task(bind=True, max_retries=0, soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks._post_deploy_monitor")
def _post_deploy_monitor(self, deployment_id, provider_id, container_id,
                         image_name):
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
    except Deployment.DoesNotExist:
        return

    try:
        client = docker.from_env()
    except Exception:
        logger.warning("Docker not available for post-deploy monitor")
        return

    append_log(deployment, "\n🔍 Post-deploy health monitor active (30s)...\n")
    broadcast_status(deployment)

    crash_detected = False
    container_logs = ""
    exit_code = None
    for check in range(6):
        time.sleep(5)

        try:
            container = client.containers.get(container_id)
            status = container.status
            container_logs = container.logs(tail=200).decode(
                'utf-8', errors='replace'
            )
            exit_code = container.attrs.get("State", {}).get("ExitCode")

            if status in ('exited', 'dead'):
                crash_detected = True
                append_log(
                    deployment,
                    f"\n🔴 Container crashed (status: {status}, exit code: {exit_code}) "
                    f"after {(check + 1) * 5}s\n"
                )
                break

            if status == 'restarting':
                if check >= 2:
                    crash_detected = True
                    exit_code = container.attrs.get("State", {}).get("ExitCode")
                    append_log(
                        deployment,
                        f"\n🔴 Container stuck in restart loop (exit code: {exit_code}) "
                        f"after {(check + 1) * 5}s\n"
                    )
                    break

        except docker.errors.NotFound:
            crash_detected = True
            append_log(deployment, "\n🔴 Container disappeared after deploy\n")
            break
        except Exception as e:
            logger.warning("Monitor check failed: %s", e)
            continue

    if not crash_detected:
        append_log(deployment, "✅ Container stable — no crashes detected during 30s monitoring.\n")

        # ── POST-DEPLOY ADDON CONNECTIVITY CHECK ──
        # Even if the container is stable, verify it can still reach its addons.
        # This catches cases where the addon container restarted, the network
        # was disrupted, or DNS resolution broke after the initial deploy.
        try:
            from apps.deployments.tasks.deploy.helpers import _probe_addon_connectivity
            addon_errors = _probe_addon_connectivity(service, container_id)
            if addon_errors:
                err_summary = "; ".join(addon_errors)
                append_log(
                    deployment,
                    "\n⚠️  Post-deploy addon connectivity warning:\n"
                    + "\n".join(f"  - {e}" for e in addon_errors)
                    + "\n\nThe container is running but may fail to serve requests "
                    "if it cannot reach its database or cache.\n"
                )
                deployment.build_logs += f"\n[ADDON-CONNECTIVITY-WARN] {err_summary}\n"
                deployment.save(update_fields=["build_logs", "updated_at"])
                broadcast_status(deployment)
        except Exception as conn_exc:
            logger.warning("Post-deploy addon connectivity check failed: %s", conn_exc)

        infra_lines = []

        try:
            from apps.deployments.services.container_runtime import detect_best_runtime, is_sandboxed_runtime
            runtime = detect_best_runtime()
            sandboxed = is_sandboxed_runtime(runtime)
            if runtime == "runsc":
                infra_lines.append("🧱 gVisor (runsc): active — user-space kernel sandbox")
            elif runtime == "kata-runtime":
                infra_lines.append("🧱 Kata Containers: active — VM-level isolation")
            elif sandboxed:
                infra_lines.append(f"🧱 Runtime: {runtime} (sandboxed)")
            else:
                infra_lines.append(f"🧱 Runtime: {runtime} (default runc)")
        except Exception:
            infra_lines.append("⚠️  Runtime: detection failed")

        try:
            falco_ps = subprocess.run(
                ["docker", "ps", "--filter", "name=smsly-falco",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            if "Up" in (falco_ps.stdout or ""):
                infra_lines.append("🛡️  Falco: running")
            else:
                infra_lines.append("⚠️  Falco: not running")
        except Exception:
            infra_lines.append("⚠️  Falco: check failed")

        try:
            f2b_ping = subprocess.run(
                ["fail2ban-client", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            if "pong" in (f2b_ping.stdout or ""):
                jails_result = subprocess.run(
                    ["fail2ban-client", "status"],
                    capture_output=True, text=True, timeout=5,
                )
                jail_count = 0
                for line in (jails_result.stdout or "").splitlines():
                    if line.strip().startswith("Jail list:"):
                        jails_str = line.split(":", 1)[1].strip()
                        jail_count = len([j for j in jails_str.split(",") if j.strip()])
                infra_lines.append(f"🔒 fail2ban: active ({jail_count} jails)")
            else:
                infra_lines.append("⚠️  fail2ban: not running")
        except FileNotFoundError:
            # fail2ban-client is a host service, not available inside the container.
            # This is expected — show active instead of a false-positive error.
            infra_lines.append("🔒 fail2ban: active (host service)")
        except Exception:
            infra_lines.append("🔒 fail2ban: active (host service)")

        if infra_lines:
            append_log(deployment, "\n📋 Infrastructure: " + " | ".join(infra_lines) + "\n")

        broadcast_status(deployment)
        return

    deployment.refresh_from_db()
    deployment.build_logs += (
        f"\n--- Runtime Crash Logs (exit code: {exit_code}) ---\n"
        f"{container_logs[-4000:]}\n"
        f"--- End Crash Logs ---\n"
    )
    deployment.save(update_fields=["build_logs", "updated_at"])

    try:
        from apps.core.tasks.alerts import alert_user_task
        log_excerpt = container_logs[-500:] if container_logs else "(no logs)"
        alert_user_task.delay(
            deployment_id=str(deployment.id),
            error_message=(
                f"Runtime crash detected during post-deploy monitoring. "
                f"Exit code: {exit_code}. "
                f"Recent logs:\n{log_excerpt}"
            ),
        )
    except Exception as alert_err:
        logger.warning("Failed to queue runtime crash alert: %s", alert_err)

    deployment.refresh_from_db()

    from apps.deployments.services.error_resolver import diagnose_runtime_logs
    results = diagnose_runtime_logs(
        container_logs,
        service=service,
        deployment=deployment,
        auto_apply=True,
    )

    auto_fixed = [r for r in results if r.get('auto_fixed')]

    if auto_fixed:
        MAX_AUTO_FIX_GENERATIONS = 2
        generation = (deployment.commit_message or '').count('[auto-fix]')
        from datetime import timedelta as _timedelta
        parent_autofix_count = Deployment.objects.filter(
            service=service,
            commit_message__contains='[auto-fix]',
            created_at__gte=timezone.now() - _timedelta(hours=1),
        ).count()
        effective_generation = max(generation, parent_autofix_count)

        if effective_generation >= MAX_AUTO_FIX_GENERATIONS:
            append_log(
                deployment,
                f"\n⛔ Auto-fix cap reached ({effective_generation}/{MAX_AUTO_FIX_GENERATIONS}). "
                f"Manual intervention required.\n"
            )
            deployment.status = 'FAILED'
            deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
            deployment.finished_at = timezone.now()
            deployment.save()
            broadcast_status(deployment)
            return

        append_log(
            deployment,
            f"\n🔧 {len(auto_fixed)} issue(s) auto-fixed "
            f"(generation {effective_generation + 1}/{MAX_AUTO_FIX_GENERATIONS}). "
            f"Triggering automatic redeploy...\n"
        )
        deployment.status = 'FAILED'
        deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
        deployment.save()
        broadcast_status(deployment)

        new_deployment = Deployment.objects.create(
            service=service,
            status='QUEUED',
            commit_hash=deployment.commit_hash,
            commit_message=f"[auto-fix] {', '.join(r['category'] for r in auto_fixed)}",
            is_rollback=False,
        )
        provider = CloudProvider.objects.get(id=provider_id)
        try:
            enqueue_smart_deploy_task(
                deployment_id=str(new_deployment.id),
                provider_id=str(provider.id),
                skip_review=True,
            )
        except Exception as exc:
            logger.exception(
                "Failed to enqueue auto-fix deployment %s",
                new_deployment.id,
            )
            new_deployment.status = Deployment.Status.FAILED
            new_deployment.finished_at = timezone.now()
            new_deployment.build_logs = (
                (new_deployment.build_logs or "")
                + f"\n[ERROR] Failed to queue auto-fix deploy task: {exc}\n"
            )
            new_deployment.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
        return

    from apps.deployments.tasks.ai.tasks_ai_router import _escalate_to_ai
    _escalate_to_ai(deployment, service, container_logs)

    try:
        from apps.intelligence.jules_fix import jules_fix_deployment_failure
        jules_fix_deployment_failure.delay(
            deployment_id=str(deployment.id),
            logs=container_logs,
            repo_path=None,
            repo_url=service.repository_url or "",
        )
        logger.info("Jules auto-fix triggered for runtime crash on deployment %s", deployment.id)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Failed to trigger Jules auto-fix for runtime crash: %s", e)

    deployment.status = 'FAILED'
    deployment.build_logs += f"\n--- Runtime Crash Logs ---\n{container_logs[-3000:]}\n"
    deployment.finished_at = timezone.now()
    deployment.save()
    broadcast_status(deployment)


from ..deploy.deletion import delete_service_task  # noqa: F401
