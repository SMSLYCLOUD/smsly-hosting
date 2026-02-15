"""Tasks module."""
from celery import shared_task
from django.utils import timezone
from django.conf import settings
import logging

# Register ecosystem tasks with Celery autodiscovery
from . import tasks_ecosystem  # noqa: F401
import os
import re
import tempfile
import shutil
import git
from urllib.parse import urlparse
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.services.git import GitManager
from apps.cloud.models import CloudProvider

logger = logging.getLogger(__name__)

# AI diagnosis task — imported at top level to avoid circular import issues
# Called asynchronously on deployment failures to provide AI-assisted debugging
try:
    from apps.deployments.tasks_ai import analyze_failure_task
except ImportError:
    analyze_failure_task = None


def _extract_dockerfile_arg_names(dockerfile_path: str) -> set[str]:
    """
    Extract build-arg names declared via `ARG ...` in a Dockerfile.

    Why: passing every env var as a build-arg leaks secrets into build logs and
    can break builds due to extremely long CLI argument lists.
    """
    arg_names: set[str] = set()
    try:
        with open(dockerfile_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.upper().startswith("ARG "):
                    continue
                # Syntax: ARG name[=default]
                arg_def = line[4:].strip()
                if not arg_def:
                    continue
                name = arg_def.split("=", 1)[0].strip()
                name = name.split()[0].strip()
                if name:
                    arg_names.add(name)
    except Exception:
        return set()
    return arg_names


def _redact_values(text: str, values: list[str]) -> str:
    """Best-effort log redaction for secret values."""
    if not text:
        return text

    redacted = text
    for val in values:
        if not val:
            continue
        # Avoid overly aggressive redaction for tiny tokens.
        if len(val) < 4:
            continue
        redacted = redacted.replace(val, "***")

    # Also redact any `--build-arg KEY=...` value portion for common secret key names.
    # Coarse on purpose: it's better to over-redact than leak.
    redacted = re.sub(
        r"(--build-arg\s+(?:[A-Z0-9_]*?(?:SECRET|TOKEN|PASSWORD|KEY|DSN)[A-Z0-9_]*?)=)([^\s]+)",
        r"\1***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _get_github_oauth_token_for_user(user):
    """
    Return the linked GitHub OAuth token for the given user (if connected).

    Used for private repo clones via HTTPS. This depends on django-allauth
    storing tokens (SOCIALACCOUNT_STORE_TOKENS=True).
    """
    if not user:
        return None

    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        return None

    account = (
        SocialAccount.objects.filter(user=user, provider="github")
        .order_by("-id")
        .first()
    )
    if not account:
        return None

    token = (
        SocialToken.objects.filter(account=account)
        .order_by("-id")
        .first()
    )
    return getattr(token, "token", None) or None


# ==============================================================================
# Real-time log broadcasting helper
# ==============================================================================

def _broadcast_log(deployment, log_line):
    """
    Append log line to deployment and broadcast via WebSocket channel layer.
    Safe to call from sync Celery tasks.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'build_log',
                    'log': log_line,
                    'status': deployment.status,
                    'timestamp': timezone.now().isoformat(),
                }
            )
    except Exception as e:
        # Never fail a deployment because of a log broadcast error
        logger.debug("Failed to broadcast log: %s", e)


def _broadcast_status(deployment):
    """Broadcast deployment status change via WebSocket."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'status_change',
                    'status': deployment.status,
                    'finished_at': (
                        deployment.finished_at.isoformat()
                        if deployment.finished_at else ''
                    ),
                    'duration_seconds': deployment.duration_seconds,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast status: %s", e)


# ==============================================================================
# Smart Multi-Cloud Deployment
# ==============================================================================


@shared_task(
    bind=True,
    max_retries=3,
    soft_time_limit=540,   # Graceful timeout at 9 minutes
    time_limit=660,        # Hard kill at 11 minutes
)
def smart_deploy_task(self, deployment_id: str, provider_id: str):
    """
    Orchestrates a deployment to any cloud provider with REAL Build Pipeline.

    1. Clone Git Repo (if Git source).
    2. Build Image via Nixpacks.
    3. Push to Registry.
    4. Deploy Container.

    Broadcasts build logs in real-time via WebSocket channel layer.
    """
    from apps.deployments.models import Deployment, Service

    source_dir = None
    deployment = None

    try:
        deployment = Deployment.objects.get(id=deployment_id)
        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save()
        _broadcast_status(deployment)

        # Step 1: Build Pipeline
        image_name = service.docker_image

        if service.deploy_type == 'GIT':
            try:
                # Create temporary build directory
                build_dir = tempfile.mkdtemp(
                    prefix=f"build_{deployment.id}_")

                # A. Clone Repository
                log_line = f"Cloning {service.repository_url}...\n"
                logger.info(
                    "Cloning repository: %s (branch: %s)",
                    service.repository_url, service.branch)
                deployment.build_logs = log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

                repo_token = None
                try:
                    parsed = urlparse(service.repository_url or "")
                    if parsed.scheme in ("http", "https") and (parsed.hostname or "").lower().endswith("github.com"):
                        repo_token = _get_github_oauth_token_for_user(getattr(service, "owner", None))
                        if repo_token:
                            log_line = "Using linked GitHub account for private repo access...\n"
                            deployment.build_logs += log_line
                            deployment.save()
                            _broadcast_log(deployment, log_line)
                except Exception:
                    repo_token = None

                source_dir = GitManager.clone_repo(
                    repo_url=service.repository_url,
                    branch=service.branch or 'main',
                    destination=build_dir,
                    token=repo_token,
                )

                # Get commit hash from cloned repo
                repo = git.Repo(source_dir)
                deployment.commit_hash = repo.head.commit.hexsha
                deployment.commit_message = repo.head.commit.message
                deployment.save()

                log_line = (
                    f"✓ Cloned successfully. "
                    f"Commit: {deployment.commit_hash[:7]}\n")
                deployment.build_logs += log_line
                deployment.save()

                # ── AI Intelligent Pre-Deploy Analysis (Automatic) ──
                try:
                    from apps.intelligence.providers import ask_with_fallback
                    from apps.intelligence.scanner import RepoScanner

                    # Auto-scan the entire codebase for configs, env vars, and issues
                    scanner = RepoScanner(source_dir)
                    ai_context = scanner.build_ai_context()

                    ai_prompt = (
                        f"You are a DevOps AI assistant. Analyze this repo for deployment.\n"
                        f"Service: {service.name}\n"
                        f"Branch: {service.branch or 'main'}\n"
                        f"Commit: {deployment.commit_hash[:7]}\n\n"
                        f"{ai_context}\n\n"
                        f"Respond with:\n"
                        f"1. Detected stack and runtime\n"
                        f"2. Any missing configs or potential issues\n"
                        f"3. Required environment variables status\n"
                        f"4. Resource/performance suggestions\n"
                        f"5. If this is a monorepo, which subdirectory should be the root\n"
                        f"Keep it concise — max 200 words."
                    )
                    ai_response, ai_provider = ask_with_fallback(ai_prompt)
                    ai_log = f"\n🤖 AI Pre-Deploy Analysis (via {ai_provider}):\n{ai_response}\n\n"
                    deployment.build_logs += ai_log
                    deployment.ai_diagnosis = ai_response
                    deployment.save()
                    _broadcast_log(deployment, ai_log)
                    logger.info("AI pre-deploy analysis complete for %s via %s", deployment_id, ai_provider)

                except Exception as ai_err:
                    logger.warning("AI pre-deploy analysis failed (non-fatal): %s", ai_err)
                    ai_log = "\n🤖 AI analysis skipped (no provider available)\n\n"
                    deployment.build_logs += ai_log
                    deployment.save()
                    _broadcast_log(deployment, ai_log)
                _broadcast_log(deployment, log_line)

                # ── Auto-Inject Detected Environment Variables ──
                try:
                    from apps.intelligence.scanner import RepoScanner as _RS
                    from apps.deployments.models import EnvironmentVariable
                    import secrets as _secrets

                    _scanner = _RS(source_dir)
                    _scan = _scanner.scan()
                    detected_vars = _scan.get('env_vars', [])

                    if detected_vars:
                        # Smart defaults for common env vars
                        def _default_value(key):
                            key_upper = key.upper()
                            if 'SECRET_KEY' in key_upper:
                                return _secrets.token_urlsafe(50)
                            if key_upper == 'DATABASE_URL' or key_upper == 'DB_URL':
                                return 'postgresql://user:password@db:5432/dbname'
                            if key_upper == 'REDIS_URL':
                                return 'redis://redis:6379/0'
                            if key_upper == 'MONGODB_URI':
                                return 'mongodb://mongo:27017/dbname'
                            if key_upper == 'PORT':
                                stack = _scan.get('stack', '')
                                return '8000' if 'Django' in stack or 'Python' in stack else '3000'
                            if key_upper == 'NODE_ENV':
                                return 'production'
                            if key_upper == 'DEBUG':
                                return 'false'
                            return 'CHANGE_ME'

                        injected, skipped = 0, 0
                        for var_name in detected_vars:
                            _, created = EnvironmentVariable.objects.get_or_create(
                                service=service,
                                key=var_name,
                                defaults={
                                    'value': _default_value(var_name),
                                    'is_secret': True,
                                },
                            )
                            if created:
                                injected += 1
                            else:
                                skipped += 1

                        env_log = (
                            f"\n🔧 Auto-injected {injected} env vars "
                            f"({skipped} already set by user)\n"
                        )
                        deployment.build_logs += env_log
                        deployment.save()
                        _broadcast_log(deployment, env_log)
                        logger.info(
                            "Env auto-injection for %s: %d injected, %d skipped",
                            deployment_id, injected, skipped,
                        )

                except Exception as env_err:
                    logger.warning("Env auto-injection failed (non-fatal): %s", env_err)

                # B. Build image — Dockerfile preferred, Nixpacks fallback
                local_tag = (
                    f"smsly/{service.name}:"
                    f"{deployment.commit_hash[:7]}")

                # Detect best build strategy — check root first, then subdirs
                # Respect monorepo root_directory if set.
                build_context_dir = source_dir
                try:
                    root_dir = (getattr(service, "root_directory", "/") or "/").strip()
                    if root_dir not in ("", "/", ".", "./"):
                        rel_root = root_dir.lstrip("/\\")
                        candidate = os.path.abspath(os.path.join(source_dir, rel_root))
                        source_abs = os.path.abspath(source_dir)
                        if not (candidate == source_abs or candidate.startswith(source_abs + os.sep)):
                            raise ValueError("root_directory must be inside the cloned repo")
                        if not os.path.isdir(candidate):
                            raise ValueError(f"Directory not found: {root_dir}")
                        build_context_dir = candidate

                        log_line = f"\nUsing root_directory: {root_dir}\n"
                        deployment.build_logs += log_line
                        deployment.save()
                        _broadcast_log(deployment, log_line)
                except Exception as root_err:
                    log_line = f"\nWARNING: invalid root_directory; using repo root ({root_err})\n"
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)

                env_var_objs = list(service.env_vars.all())
                build_env_vars = {env.key: env.value for env in env_var_objs}
                secret_values = [
                    env.value for env in env_var_objs
                    if (
                        getattr(env, "is_secret", False)
                        or re.search(
                            r"(SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|API_KEY)",
                            str(getattr(env, "key", "") or ""),
                            re.IGNORECASE,
                        )
                    )
                ]

                dockerfile_path = os.path.join(build_context_dir, "Dockerfile")
                has_dockerfile = os.path.isfile(dockerfile_path)

                # If no root Dockerfile, search one level deep
                if not has_dockerfile:
                    for entry in os.listdir(build_context_dir):
                        candidate = os.path.join(build_context_dir, entry, "Dockerfile")
                        if os.path.isdir(os.path.join(build_context_dir, entry)) and os.path.isfile(candidate):
                            dockerfile_path = candidate
                            has_dockerfile = True
                            logger.info("Found Dockerfile in subdirectory: %s", candidate)
                            break

                if has_dockerfile:
                    log_line = f"\nDockerfile detected — building image {local_tag} via Docker...\n"
                    logger.info("Building image with Docker: %s", local_tag)
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)

                    # Prepare build args from env vars:
                    # only pass args declared in the Dockerfile (prevents leaking secrets into build logs).
                    dockerfile_arg_names = _extract_dockerfile_arg_names(dockerfile_path)
                    build_args = []
                    if dockerfile_arg_names:
                        arg_keys = [k for k in sorted(dockerfile_arg_names) if k in build_env_vars]
                        for k in arg_keys:
                            build_args.extend(["--build-arg", f"{k}={build_env_vars[k]}"])

                        arg_preview = ", ".join(arg_keys[:15])
                        if len(arg_keys) > 15:
                            arg_preview += f", ...(+{len(arg_keys) - 15})"
                        log_line = f"Using Dockerfile ARG build-args: {arg_preview or '(none)'}\n"
                        deployment.build_logs += log_line
                        deployment.save()
                        _broadcast_log(deployment, log_line)
                    else:
                        # Fallback: pass only common public build-time vars.
                        for k, v in build_env_vars.items():
                            if k.startswith(("NEXT_PUBLIC_", "PUBLIC_", "VITE_")):
                                build_args.extend(["--build-arg", f"{k}={v}"])

                    import subprocess
                    docker_cmd = [
                        "docker", "build",
                        "-t", local_tag,
                        "-f", dockerfile_path,
                        *build_args,
                        build_context_dir,
                    ]

                    try:
                        process = subprocess.run(
                            docker_cmd, check=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True, timeout=600,
                        )
                        build_result = {
                            "image_name": local_tag,
                            "stdout": _redact_values(process.stdout or "", secret_values),
                            "stderr": _redact_values(process.stderr or "", secret_values),
                        }
                    except subprocess.CalledProcessError as e:
                        stdout = _redact_values(getattr(e, "stdout", "") or "", secret_values)
                        stderr = _redact_values(getattr(e, "stderr", "") or "", secret_values)
                        error_detail = ""
                        if stdout:
                            error_detail += f"\n--- Build Output ---\n{stdout[-3000:]}"
                        if stderr:
                            error_detail += f"\n--- Build Errors ---\n{stderr[-3000:]}"
                        raise RuntimeError(f"Docker build failed:{error_detail}") from e

                else:
                    log_line = f"\nNo Dockerfile found — building image {local_tag} via Nixpacks...\n"
                    logger.info("Building image with Nixpacks: %s", local_tag)
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)

                    # Build the image
                    build_result = NixpacksBuilder.build_image(
                        source_dir=build_context_dir,
                        image_name=local_tag,
                        env_vars=build_env_vars
                    )

                # Capture build output for debugging (redacted).
                build_stdout = (
                    _redact_values(build_result.get("stdout", ""), secret_values)
                    if isinstance(build_result, dict) else ""
                )
                build_stderr = (
                    _redact_values(build_result.get("stderr", ""), secret_values)
                    if isinstance(build_result, dict) else ""
                )

                if build_stdout or build_stderr:
                    output = ""
                    if build_stdout:
                        output += f"\n--- Build Output ---\n{build_stdout[-3000:]}\n"
                    if build_stderr:
                        output += f"\n--- Build Errors ---\n{build_stderr[-3000:]}\n"
                    deployment.build_logs += output
                    deployment.save()
                    _broadcast_log(deployment, output)

                log_line = f"✓ Successfully built {local_tag}\n"
                deployment.build_logs += log_line
                deployment.save()
                _broadcast_log(deployment, log_line)

                # C. Push to Registry (if configured)
                registry_url = getattr(
                    settings, 'CONTAINER_REGISTRY_URL', None)
                if registry_url:
                    log_line = f"\nPushing to {registry_url}...\n"
                    logger.info(
                        "Pushing image to registry: %s", registry_url)
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)

                    remote_tag = NixpacksBuilder.push_image(
                        local_tag, registry_url)
                    image_name = remote_tag

                    log_line = f"✓ Pushed to {remote_tag}\n"
                    deployment.build_logs += log_line
                    deployment.save()
                    _broadcast_log(deployment, log_line)
                else:
                    # Use local image if no registry configured
                    image_name = local_tag
                    logger.info(
                        "No registry configured, using local image")

            except Exception as e:
                error_msg = f"Build pipeline failed: {str(e)}"
                logger.error(error_msg)
                log_line = f"\n✗ {error_msg}\n"
                deployment.build_logs += log_line
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.save()
                _broadcast_log(deployment, log_line)
                _broadcast_status(deployment)

                # Trigger AI diagnosis on failure
                if analyze_failure_task:
                    try:
                        analyze_failure_task.delay(str(deployment.id))
                        ai_log = "\n🤖 AI diagnosis requested...\n"
                        deployment.build_logs += ai_log
                        deployment.save()
                        _broadcast_log(deployment, ai_log)
                    except Exception:
                        pass

                # Cleanup on build failure
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                    logger.info(
                        "Cleaned up build directory after failure: %s",
                        source_dir)

                raise self.retry(exc=e, countdown=30)

        # Step 2: Deploy
        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()
        _broadcast_status(deployment)

        log_line = "\nDeploying container...\n"
        deployment.build_logs += log_line
        deployment.save()
        _broadcast_log(deployment, log_line)

        compute = ComputeService(provider)

        # Prepare Env Vars
        env_vars = {env.key: env.value for env in service.env_vars.all()}

        # Normalize replicas to a safe value for runtime deployment.
        requested_replicas = service.min_replicas
        try:
            replicas = int(requested_replicas)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid replicas value for service %s: %r. Falling back to 1.",
                service.name, requested_replicas
            )
            replicas = 1
        if replicas < 1:
            logger.warning(
                "Replicas must be >= 1 for service %s. Received %s; using 1.",
                service.name, replicas
            )
            replicas = 1

        # Call Universal Adapter
        resource = compute.deploy_container(
            name=service.name,
            image=image_name,
            env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024),
            memory=service.memory_mb,
            replicas=replicas
        )

        # Step 3: Success
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.container_id = resource.resource_id
        deployment.save()

        log_line = (
            f"✓ Deployment successful! Container: "
            f"{resource.resource_id[:12]}\n"
            f"  Duration: {deployment.duration_seconds:.1f}s\n")
        deployment.build_logs += log_line
        deployment.save()
        _broadcast_log(deployment, log_line)
        _broadcast_status(deployment)

        logger.info(
            "Deployment %s successful on %s",
            deployment_id, provider.name)

        # Cleanup temporary build directory on success
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info("Cleaned up build directory: %s", source_dir)

    except Exception as e:
        logger.error("Deployment %s failed: %s", deployment_id, e)
        if deployment is not None:
            deployment.status = Deployment.Status.FAILED
            deployment.finished_at = timezone.now()
            log_line = f"\n✗ Deployment failed: {str(e)}\n"
            deployment.build_logs += log_line
            deployment.save()
            _broadcast_log(deployment, log_line)
            _broadcast_status(deployment)

            # Trigger AI diagnosis on failure
            if analyze_failure_task:
                try:
                    analyze_failure_task.delay(str(deployment.id))
                    ai_log = "\n🤖 AI diagnosis requested...\n"
                    deployment.build_logs += ai_log
                    deployment.save()
                    _broadcast_log(deployment, ai_log)
                except Exception:
                    pass

        # Cleanup on failure
        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)
            logger.info(
                "Cleaned up build directory after failure: %s",
                source_dir)

        raise self.retry(exc=e, countdown=30)

# ==============================================================================
# One-Click Template Deploy (Addons + Deploy Orchestration)
# ==============================================================================


@shared_task(bind=True, max_retries=0)
def one_click_deploy_template_task(self, service_id: str, template_id: str):
    """
    Background orchestration for template deployments.

    Why this exists:
    - Addon provisioning injects env vars into the Service record.
      Those env vars MUST exist before the container is launched, otherwise the
      running container will never see the injected values.
    - This task provisions required addons first, then triggers the deployment.
    """
    from apps.deployments.models import Service, Deployment, EnvironmentVariable
    from apps.deployments.models_addons import Addon
    from services.addon_provisioner import addon_provisioner

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        logger.error("one_click_deploy_template_task: service not found: %s", service_id)
        return None

    # Load template definition from fixtures
    try:
        import json
        template_path = os.path.join(
            settings.BASE_DIR, 'apps/deployments/fixtures/templates.json'
        )
        with open(template_path, 'r', encoding='utf-8') as f:
            templates = json.load(f)
        template = next((t for t in templates if t.get('id') == template_id), None)
    except Exception as e:
        logger.error("one_click_deploy_template_task: failed to load template %s: %s", template_id, e)
        template = None

    required_addons = []
    if isinstance(template, dict):
        ra = template.get('required_addons') or []
        if isinstance(ra, list):
            required_addons = [str(x) for x in ra if x]

    # Provision required addons synchronously so env vars are ready before deploy.
    env_key_map = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }

    for addon_type in required_addons:
        if addon_type not in (a[0] for a in Addon.Type.choices):
            logger.warning("Skipping unsupported addon type %s for service %s", addon_type, service.id)
            continue

        addon = Addon.objects.create(
            service=service,
            name=f"{addon_type.lower()}-{service.name}"[:255],
            addon_type=addon_type,
            status=Addon.Status.PROVISIONING,
        )

        try:
            container_id, connection_url = addon_provisioner.provision(addon)
            addon.connection_url = connection_url
            addon.status = Addon.Status.ACTIVE
            addon.coolify_uuid = container_id
            addon.save()

            env_key = env_key_map.get(addon.addon_type, f"{addon.addon_type}_URL")
            EnvironmentVariable.objects.update_or_create(
                service=service,
                key=env_key,
                defaults={'value': connection_url, 'is_secret': True},
            )
        except Exception as e:
            addon.status = Addon.Status.FAILED
            addon.save()
            logger.error("Addon provisioning failed (%s) for service %s: %s", addon_type, service.id, e)
            # Fail-closed: do not deploy a template that is missing required deps.
            return None

    # Trigger deployment after deps are ready.
    provider = service.provider if service.provider and service.provider.is_active else None
    if not provider:
        provider = CloudProvider.objects.filter(is_active=True).first()
    if not provider:
        logger.error("one_click_deploy_template_task: no active provider configured for service %s", service.id)
        return None

    deployment = Deployment.objects.create(
        service=service,
        status=Deployment.Status.QUEUED,
        commit_hash='template',
        commit_message=f"Template Deploy: {template_id}",
    )
    smart_deploy_task.delay(str(deployment.id), str(provider.id))
    return str(deployment.id)

# ==============================================================================
# LEGACY: Addon Provisioning (Docker-native)
# ==============================================================================


@shared_task(bind=True, max_retries=3)
def provision_addon_task(self, addon_id: str):
    """
    Provision a database addon using Docker containers.
    """
    from apps.deployments.models_addons import Addon
    from apps.deployments.models import EnvironmentVariable
    from services.addon_provisioner import addon_provisioner

    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }

    try:
        addon = Addon.objects.get(id=addon_id)
        logger.info(
            "Provisioning addon %s (%s)",
            addon.name, addon.addon_type)

        # Create container via Docker
        container_id, connection_url = addon_provisioner.provision(addon)

        addon.connection_url = connection_url
        addon.status = Addon.Status.ACTIVE
        addon.coolify_uuid = container_id
        addon.save()

        # Inject connection URL if attached to a service
        if addon.service:
            env_key = ENV_KEY_MAP.get(
                addon.addon_type, f"{addon.addon_type}_URL")
            EnvironmentVariable.objects.update_or_create(
                service=addon.service,
                key=env_key,
                defaults={'value': connection_url, 'is_secret': True}
            )

        logger.info("Addon %s provisioned successfully", addon.name)

    except Exception as e:
        logger.error("Failed to provision addon %s: %s", addon_id, e)
        raise self.retry(exc=e, countdown=30)


@shared_task
def deprovision_addon_task(addon_id: str):
    """Delete addon container."""
    from apps.deployments.models_addons import Addon
    from apps.deployments.models import EnvironmentVariable
    from services.addon_provisioner import addon_provisioner

    try:
        addon = Addon.objects.get(id=addon_id)
        if addon.coolify_uuid:
            addon_provisioner.deprovision(
                addon.coolify_uuid, f"addon-{addon.id}")

        addon.status = Addon.Status.DELETED
        addon.save()
    except Exception as e:
        logger.error("Failed to deprovision: %s", e)


@shared_task(bind=True, max_retries=3)
def backup_addon_task(self, addon_id: str):
    """
    Create a backup for the specified addon.
    """
    from apps.deployments.models_addons import Addon, Backup
    from services.addon_provisioner import addon_provisioner
    
    try:
        addon = Addon.objects.get(id=addon_id)
        logger.info(f"Starting backup for {addon.name}")
        
        # Create pending backup record
        backup = Backup.objects.create(
            addon=addon,
            status=Backup.Status.PENDING
        )
        
        try:
            # Execute backup
            file_path = addon_provisioner.create_backup(addon)
            
            # Update record
            backup.file_path = file_path
            if os.path.exists(file_path):
                backup.size_bytes = os.path.getsize(file_path)
            backup.status = Backup.Status.COMPLETED
            backup.completed_at = timezone.now()
            backup.save()
            
            logger.info(f"Backup {backup.id} created for {addon.name} at {file_path}")
            return str(backup.id)
            
        except Exception as e:
            backup.status = Backup.Status.FAILED
            backup.error_message = str(e)
            backup.save()
            raise e
            
    except Exception as e:
        logger.error(f"Backup task failed for {addon_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def restore_addon_task(self, backup_id: str):
    """
    Restore a backup to the addon.
    WARNING: This overwrites current data.
    """
    from apps.deployments.models_addons import Backup
    from services.addon_provisioner import addon_provisioner
    
    try:
        backup = Backup.objects.get(id=backup_id)
        addon = backup.addon
        
        logger.info(f"Restoring backup {backup.id} to {addon.name}")
        
        addon_provisioner.restore_backup(addon, backup.file_path)
        
        logger.info(f"Restore complete for {addon.name}")
        return True
        
    except Exception as e:
        logger.error(f"Restore task failed for {backup_id}: {e}")
        raise e
