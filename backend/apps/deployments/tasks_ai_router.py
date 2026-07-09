import logging

logger = logging.getLogger(__name__)
SHARED_OLLAMA_RAM_FRACTION = 0.25  # 25% of total host RAM
SHARED_OLLAMA_MIN_RAM_MB = 2048    # 2 GB — minimum viable for any LLM
SHARED_OLLAMA_MAX_RAM_MB = 8192    # 8 GB — practical ceiling on most VPS
SHARED_OLLAMA_MIN_CPU_CORES = 1.0
SHARED_OLLAMA_MAX_CPU_CORES = 4.0
SHARED_OLLAMA_NAME_PREFIX = "ollama-cpp-shared"
SHARED_OLLAMA_PORT = 11434

import logging
import shlex
import subprocess

from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Service,
)
from apps.deployments.utils import (
    append_log,
)


def _escalate_to_ai(deployment, service, container_logs):
    """
    Escalate an unknown runtime error to AI models with full code context.
    Uses all configured AI providers via ask_with_fallback.
    """
    try:
        from apps.intelligence.providers import ask_with_fallback

        # Build rich context: logs + service info + env vars (masked)
        env_summary = ", ".join(
            f"{ev.key}={'***' if ev.is_secret else ev.value}"
            for ev in service.env_vars.all()
        )

        prompt = (
            f"A deployed container for service '{service.name}' crashed immediately "
            f"after deployment. Analyze the logs and provide:\n"
            f"1. Root cause of the crash\n"
            f"2. Specific fix (env var to add, config to change, code to fix)\n"
            f"3. Whether this can be auto-fixed by the platform\n\n"
            f"Service: {service.name}\n"
            f"Deploy type: {service.deploy_type}\n"
            f"Image: {service.docker_image or 'built from git'}\n"
            f"Git repo: {service.repository_url}\n"
            f"Env vars: {env_summary}\n\n"
            f"--- CONTAINER LOGS (last 200 lines) ---\n"
            f"{container_logs[-4000:]}\n"
            f"--- END LOGS ---\n\n"
            f"Return a JSON object:\n"
            f'{{\n'
            f'  "root_cause": "Brief description",\n'
            f'  "fix": "Specific actionable fix",\n'
            f'  "env_vars_needed": {{"KEY": "value_or_empty"}},\n'
            f'  "auto_fixable": true/false,\n'
            f'  "severity": "critical/warning/info"\n'
            f'}}\n'
        )

        response, provider_name = ask_with_fallback(prompt)
        deployment.ai_diagnosis = response
        deployment.save(update_fields=['ai_diagnosis'])

        append_log(
            deployment,
            f"\n🤖 AI Diagnosis ({provider_name}):\n{response[:2000]}\n"
        )

        # Try to parse and auto-apply AI suggestions
        from apps.deployments.utils import parse_ai_resource_recommendation
        parsed = parse_ai_resource_recommendation(response)
        if parsed and parsed.get('env_vars_needed'):
            from apps.deployments.services.error_resolver import _apply_fix
            fix = {'env': parsed['env_vars_needed']}
            import re as _re
            action = _apply_fix(fix, _re.match('', ''), '', service, deployment)
            if action:
                append_log(deployment, f"  ✅ AI-suggested fix applied: {action}\n")

    except Exception as e:
        logger.warning("AI escalation failed for deployment %s: %s",
                       deployment.id, e)
        append_log(deployment, f"\n🤖 AI diagnosis unavailable: {e}\n")



def _detect_safe_ollama_ram_mb() -> int:
    """
    Determine a safe RAM allocation for the shared Ollama CPP based on
    the host's total system memory.  Never allocates more than 25% of
    total RAM, clamped between the configured min/max.
    """
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mb = vm.total // (1024 * 1024)
        # Available is what's actually free + reclaimable (cache/buffers)
        available_mb = vm.available // (1024 * 1024)

        fraction_mb = int(total_mb * SHARED_OLLAMA_RAM_FRACTION)
        safe_mb = max(SHARED_OLLAMA_MIN_RAM_MB, min(fraction_mb, SHARED_OLLAMA_MAX_RAM_MB))

        # On a tight VPS where even 25% of total exceeds what's actually
        # available, dial back to 50% of available so the OS doesn't OOM.
        if safe_mb > available_mb * 0.5 and available_mb > 0:
            safe_mb = max(SHARED_OLLAMA_MIN_RAM_MB, int(available_mb * 0.5))

        logger.info(
            "Shared Ollama RAM: host=%dMB available=%dMB → allocated=%dMB",
            total_mb, available_mb, safe_mb,
        )
        return safe_mb
    except Exception:
        # psutil unavailable — use 4 GB as a safe middle-ground
        return 4096



def _detect_safe_ollama_cpu() -> float:
    """Detect safe CPU allocation for shared Ollama."""
    try:
        import psutil
        logical = psutil.cpu_count(logical=True) or 1
        # Give Ollama up to half the logical cores, clamped
        allocated = max(SHARED_OLLAMA_MIN_CPU_CORES,
                        min(float(logical) * 0.5, SHARED_OLLAMA_MAX_CPU_CORES))
        return round(allocated, 1)
    except Exception:
        return 2.0



def _ensure_shared_ollama_cpp(service, provider) -> str | None:
    """
    Find or create a shared Ollama CPP service for the project.
    Returns the shared service ID (str) or None if creation fails.
    Only one shared Ollama CPP is maintained per project to save VPS resources.
    """
    from apps.deployments.models import Service

    project = getattr(service, 'project', None)
    owner = getattr(service, 'owner', None)

    # 1. Look for an existing shared Ollama in the same project
    existing = Service.objects.filter(
        project=project,
        deploy_type='DOCKER',
        docker_image__contains='ollama',
    ).order_by('-created_at').first()

    # If one exists and looks active/resourced, reuse it
    if existing and existing.docker_image and 'ollama' in existing.docker_image.lower():
        if existing.status not in {'DELETION_PENDING', 'DELETING'}:
            # Ensure it has a project association
            if not existing.project and project:
                existing.project = project
                existing.save(update_fields=['project'])
            return str(existing.id)

    # 2. Auto-detect safe resource allocation from VPS
    ram_mb = _detect_safe_ollama_ram_mb()
    cpu = _detect_safe_ollama_cpu()

    # 3. Create a new shared Ollama CPP service
    try:
        import re
        project_id = str(project.id)[:8] if project else 'global'
        name = f"{SHARED_OLLAMA_NAME_PREFIX}-{project_id}"
        name = re.sub(r'[^a-z0-9-]+', '-', name.lower()).strip('-')[:63]

        # Avoid duplicates with race-condition safety
        existing = Service.objects.filter(name=name).first()
        if existing:
            return str(existing.id)

        shared = Service.objects.create(
            name=name,
            deploy_type='DOCKER',
            docker_image='ollama/ollama:latest',
            internal_port=SHARED_OLLAMA_PORT,
            owner=owner,
            provider=provider,
            project=project,
            memory_mb=ram_mb,
            cpu_cores=cpu,
            deploy_mode='SINGLE',
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='OLLAMA_HOST',
            defaults={'value': '0.0.0.0', 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='OLLAMA_KEEP_ALIVE',
            defaults={'value': '24h', 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='PORT',
            defaults={'value': str(SHARED_OLLAMA_PORT), 'is_secret': False}
        )
        EnvironmentVariable.objects.update_or_create(
            service=shared, key='PUBLIC_DOMAIN',
            defaults={'value': shared.public_domain or '', 'is_secret': False}
        )

        # Trigger deployment
        from .tasks_deploy import enqueue_smart_deploy_task
        deployment = Deployment.objects.create(
            service=shared,
            status='QUEUED',
            commit_hash='template',
            commit_message='Shared Ollama CPP (auto-deployed)'
        )
        enqueue_smart_deploy_task(
            deployment_id=str(deployment.id),
            provider_id=str(provider.id)
        )
        logger.info(
            "Shared Ollama CPP created: %s (project %s, %dMB RAM, %.1f CPU)",
            name, project_id, ram_mb, cpu,
        )
        return str(shared.id)

    except Exception as exc:
        logger.error("Failed to create shared Ollama CPP: %s", exc)
        return None



def _pull_ollama_models_into_shared(shared_ollama_id: str, models: list):
    """
    Pull Ollama models into the shared Ollama CPP container.
    Runs as a fire-and-forget background subprocess.
    """
    try:
        shared = Service.objects.get(id=shared_ollama_id)
        container_name = shared.name
        for model in models:
            if not model:
                continue
            model = str(model).strip()
            logger.info("Pulling Ollama model '%s' into shared container %s", model, container_name)
            subprocess.Popen(
                ["docker", "exec", container_name, "sh", "-lc",
                 f"ollama pull {shlex.quote(model)}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as exc:
        logger.warning("Failed to pull models into shared Ollama %s: %s", shared_ollama_id, exc)



def _cleanup_shared_ollama_if_unused(project):
    """
    After deleting a service, check if the shared Ollama CPP is still needed.
    If no remaining services in the project reference Ollama, delete it
    to free VPS resources.
    """
    if not project:
        return
    try:
        from apps.deployments.models import Service

        # Find the shared Ollama in this project
        shared = Service.objects.filter(
            project=project,
            deploy_type='DOCKER',
            docker_image__startswith='ollama/',
        ).order_by('-created_at').first()

        if not shared:
            return

        # Check: are there any OTHER services in the project that need Ollama?
        remaining = Service.objects.filter(
            project=project,
        ).exclude(
            id=shared.id
        ).exclude(
            status__in=['DELETION_PENDING', 'DELETING']
        ).exclude(
            deploy_type='DOCKER',
            docker_image__startswith='ollama/',  # skip other Ollama-only services
        )

        # Look for any service that references Ollama via env vars or docker image
        needs_ollama = False
        for svc in remaining:
            img = str(svc.docker_image or '').lower()
            if img.startswith('ollama/'):
                needs_ollama = True
                break
            # Check if env vars reference OLLAMA_BASE_URL
            if svc.env_vars.filter(key='OLLAMA_BASE_URL').exists():
                needs_ollama = True
                break
            if svc.env_vars.filter(key='OLLAMA_MODEL').exists():
                needs_ollama = True
                break

        if not needs_ollama:
            logger.info(
                "No remaining services need shared Ollama in project %s. "
                "Cleaning up %s to free VPS resources.",
                project.id, shared.name
            )
            # Mark for deletion
            shared.status = 'DELETION_PENDING'
            shared.save(update_fields=['status'])
            from .tasks_deploy import delete_service_task
            delete_service_task.delay(str(shared.id), force=True)
    except Exception as exc:
        logger.warning("Shared Ollama cleanup check failed: %s", exc)
