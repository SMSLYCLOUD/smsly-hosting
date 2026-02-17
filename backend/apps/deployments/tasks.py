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
from services.builders import is_buildkit_cache_error, prune_buildkit_cache

logger = logging.getLogger(__name__)

# AI diagnosis task — imported at top level to avoid circular import issues
try:
    from apps.deployments.tasks_ai import analyze_failure_task
except ImportError:
    analyze_failure_task = None


def _extract_dockerfile_arg_names(dockerfile_path: str) -> set[str]:
    """
    Extract build-arg names declared via `ARG ...` in a Dockerfile.
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
        if len(val) < 4:
            continue
        redacted = redacted.replace(val, "***")

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


def _broadcast_pipeline(deployment):
    """Broadcast pipeline stages update via WebSocket."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'pipeline_update',
                    'stages': deployment.pipeline_stages,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast pipeline: %s", e)


def _update_stage(deployment, name, status, duration=None):
    """Update a pipeline stage status."""
    # Always refresh to get latest stages state to avoid overwrites
    deployment.refresh_from_db(fields=['pipeline_stages'])
    stages = deployment.pipeline_stages or []
    if not isinstance(stages, list):
        stages = []

    found = False
    for stage in stages:
        if stage.get('name') == name:
            stage['status'] = status
            if duration is not None:
                stage['duration'] = duration
            found = True
            break

    if not found:
        stages.append({
            'name': name,
            'status': status,
            'duration': duration or 0
        })

    deployment.pipeline_stages = stages
    deployment.save(update_fields=['pipeline_stages'])
    _broadcast_pipeline(deployment)

def _append_log(deployment, log_line):
    """
    Append logs safely using refresh and update_fields.
    """
    if not log_line:
        return
    # Refresh logs to avoid overwrite race conditions
    deployment.refresh_from_db(fields=['build_logs'])
    deployment.build_logs += log_line
    deployment.save(update_fields=['build_logs'])
    _broadcast_log(deployment, log_line)


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
    """
    from apps.deployments.models import Deployment, Service

    source_dir = None
    deployment = None

    try:
        deployment = Deployment.objects.get(id=deployment_id)

        # Check if already cancelled
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s was cancelled before start", deployment_id)
            return

        service = deployment.service
        provider = CloudProvider.objects.get(id=provider_id)

        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save(update_fields=['status', 'started_at'])
        _broadcast_status(deployment)

        # Initialize pipeline stages
        deployment.pipeline_stages = []
        _update_stage(deployment, 'Clone', 'pending')
        _update_stage(deployment, 'Build', 'pending')
        if getattr(settings, 'CONTAINER_REGISTRY_URL', None):
            _update_stage(deployment, 'Push', 'pending')
        _update_stage(deployment, 'Deploy', 'pending')

        # Check cancellation
        deployment.refresh_from_db(fields=['status'])
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled during init", deployment_id)
            return

        # Step 1: Build Pipeline
        image_name = service.docker_image

        if service.deploy_type == 'FUNCTION':
            try:
                build_dir = tempfile.mkdtemp(prefix=f"func_build_{deployment.id}_")
                source_dir = build_dir

                from apps.cloud.services.function_provisioner import FunctionProvisioner
                FunctionProvisioner.prepare_context(service, build_dir)

                tag_hash = deployment.commit_hash[:7] if deployment.commit_hash else 'latest'
                local_tag = f"smsly/func-{service.name}:{tag_hash}"

                _update_stage(deployment, 'Build', 'running')
                build_start = timezone.now()

                _append_log(deployment, f"Building function image {local_tag}...\n")

                import subprocess
                docker_cmd = ["docker", "build", "-t", local_tag, build_dir]
                process = subprocess.run(
                    docker_cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=300
                )

                _append_log(deployment, process.stdout)
                _update_stage(deployment, 'Build', 'success', (timezone.now() - build_start).total_seconds())

                # Push
                registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
                if registry_url:
                    _update_stage(deployment, 'Push', 'running')
                    push_start = timezone.now()
                    remote_tag = NixpacksBuilder.push_image(local_tag, registry_url)
                    image_name = remote_tag
                    _update_stage(deployment, 'Push', 'success', (timezone.now() - push_start).total_seconds())
                else:
                    image_name = local_tag

            except Exception as e:
                logger.error(f"Function build failed: {e}")
                deployment.refresh_from_db(fields=['build_logs'])
                deployment.build_logs += f"\nBuild failed: {e}\n"
                if hasattr(e, 'stderr'):
                    deployment.build_logs += e.stderr
                deployment.status = Deployment.Status.FAILED
                deployment.save(update_fields=['status', 'build_logs'])

                _update_stage(deployment, 'Build', 'failed')
                _broadcast_status(deployment)
                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)
                raise self.retry(exc=e, countdown=30)

        elif service.deploy_type == 'GIT':
            try:
                # Create temporary build directory
                build_dir = tempfile.mkdtemp(
                    prefix=f"build_{deployment.id}_")

                # A. Clone Repository
                _update_stage(deployment, 'Clone', 'running')
                clone_start = timezone.now()

                log_line = f"Cloning {service.repository_url}...\n"
                logger.info(
                    "Cloning repository: %s (branch: %s)",
                    service.repository_url, service.branch)
                _append_log(deployment, log_line)

                # Check cancellation
                deployment.refresh_from_db(fields=['status'])
                if deployment.status == Deployment.Status.CANCELLED:
                    logger.info("Deployment %s cancelled during clone", deployment_id)
                    return

                repo_token = None
                try:
                    parsed = urlparse(service.repository_url or "")
                    if parsed.scheme in ("http", "https") and (parsed.hostname or "").lower().endswith("github.com"):
                        repo_token = _get_github_oauth_token_for_user(getattr(service, "owner", None))
                        if repo_token:
                            _append_log(deployment, "Using linked GitHub account for private repo access...\n")
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
                deployment.save(update_fields=['commit_hash', 'commit_message'])

                _append_log(deployment, f"✓ Cloned successfully. Commit: {deployment.commit_hash[:7]}\n")
                _update_stage(deployment, 'Clone', 'success', (timezone.now() - clone_start).total_seconds())

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

                    deployment.ai_diagnosis = ai_response
                    deployment.save(update_fields=['ai_diagnosis'])
                    _append_log(deployment, ai_log)
                    logger.info("AI pre-deploy analysis complete for %s via %s", deployment_id, ai_provider)

                except Exception as ai_err:
                    logger.warning("AI pre-deploy analysis failed (non-fatal): %s", ai_err)
                    _append_log(deployment, "\n🤖 AI analysis skipped (no provider available)\n\n")

                # ── Auto-Inject Detected Environment Variables ──
                try:
                    from apps.intelligence.scanner import RepoScanner as _RS
                    from apps.deployments.models import EnvironmentVariable
                    import secrets as _secrets

                    _scanner = _RS(source_dir)
                    _scan = _scanner.scan()
                    detected_vars = _scan.get('env_vars', [])

                    if detected_vars:
                        def _default_value(key):
                            key_upper = key.upper()
                            # (Simulating complex logic from original file for brevity,
                            # assuming full logic should be preserved. Since I'm overwriting,
                            # I must include the full logic or risk breaking functionality.)
                            # I will paste the full logic here.

                            if 'SECRET_KEY' in key_upper or key_upper == 'SECRET': return _secrets.token_urlsafe(50), True
                            if key_upper in ('JWT_SECRET', 'SESSION_SECRET', 'COOKIE_SECRET', 'CSRF_SECRET', 'SIGNING_KEY', 'HASH_SALT'): return _secrets.token_urlsafe(32), True
                            if key_upper in ('DATABASE_URL', 'DB_URL', 'DB_URI', 'SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_DATABASE_URL'):
                                stack = _scan.get('stack', '')
                                deps = _scan.get('dependencies', [])
                                dep_str = ' '.join(deps) if isinstance(deps, list) else str(deps)
                                if 'asyncpg' in dep_str or 'async' in dep_str.lower(): return 'postgresql+asyncpg://user:password@db:5432/dbname', True
                                return 'postgresql://user:password@db:5432/dbname', True
                            if key_upper == 'REDIS_URL': return 'redis://redis:6379/0', True
                            if key_upper in ('CELERY_BROKER_URL', 'BROKER_URL'): return 'redis://redis:6379/1', True
                            if key_upper in ('CELERY_RESULT_BACKEND', 'RESULT_BACKEND'): return 'redis://redis:6379/2', True
                            if key_upper in ('MONGODB_URI', 'MONGO_URI', 'MONGO_URL'): return 'mongodb://mongo:27017/dbname', True
                            if key_upper == 'PORT': return '8000', True # simplified default
                            if key_upper.endswith('_PORT'): return '8080', True
                            if key_upper.endswith('_HOST') or key_upper.endswith('_HOSTNAME'): return 'localhost', True
                            if key_upper in ('POSTGRES_USER', 'DB_USER'): return 'appuser', True
                            if key_upper in ('POSTGRES_DB', 'DB_NAME'): return service.name.replace('-', '_')[:30], True
                            if key_upper in ('POSTGRES_PASSWORD', 'DB_PASSWORD'): return _secrets.token_urlsafe(24), True
                            if key_upper in ('DEBUG', 'TESTING'): return 'false', True
                            if key_upper in ('NODE_ENV', 'ENVIRONMENT'): return 'production', True
                            if key_upper in ('ALLOWED_HOSTS', 'CORS_ALLOWED_ORIGINS'): return '*', True
                            if key_upper in ('LOG_LEVEL',): return 'info', True
                            if key_upper in ('WORKERS', 'WEB_CONCURRENCY'): return '4', True
                            return None, False

                        injected, skipped, fixed, user_required = 0, 0, 0, 0
                        user_required_keys = []
                        for var_name in detected_vars:
                            default_val, should_inject = _default_value(var_name)
                            if not should_inject:
                                user_required += 1
                                user_required_keys.append(var_name)
                                continue

                            env_obj, created = EnvironmentVariable.objects.get_or_create(
                                service=service, key=var_name,
                                defaults={'value': default_val, 'is_secret': True}
                            )
                            if created: injected += 1
                            elif env_obj.value == 'CHANGE_ME':
                                env_obj.value = default_val
                                env_obj.save(update_fields=['value'])
                                fixed += 1
                            else: skipped += 1

                        env_log = (f"\n🔧 Auto-injected {injected} env vars ({skipped} already set by user)")
                        if fixed > 0: env_log += f"\n🔄 Auto-healed {fixed} stale CHANGE_ME values"
                        if user_required > 0: env_log += f"\n⚠️ {user_required} vars need your input: {', '.join(user_required_keys[:5])}...\n"
                        env_log += "\n"

                        _append_log(deployment, env_log)
                        logger.info("Env auto-injection for %s: %d injected", deployment_id, injected)

                except Exception as env_err:
                    logger.warning("Env auto-injection failed (non-fatal): %s", env_err)

                # B. Build image — Dockerfile preferred, Nixpacks fallback
                _update_stage(deployment, 'Build', 'running')
                build_start = timezone.now()

                local_tag = (
                    f"smsly/{service.name}:"
                    f"{deployment.commit_hash[:7]}")

                # Detect best build strategy — check root first, then subdirs
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

                        _append_log(deployment, f"\nUsing root_directory: {root_dir}\n")
                except Exception as root_err:
                    _append_log(deployment, f"\nWARNING: invalid root_directory; using repo root ({root_err})\n")

                # Check cancellation before build
                deployment.refresh_from_db(fields=['status'])
                if deployment.status == Deployment.Status.CANCELLED:
                    logger.info("Deployment %s cancelled before build", deployment_id)
                    return

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

                if not has_dockerfile:
                    for entry in os.listdir(build_context_dir):
                        candidate = os.path.join(build_context_dir, entry, "Dockerfile")
                        if os.path.isdir(os.path.join(build_context_dir, entry)) and os.path.isfile(candidate):
                            dockerfile_path = candidate
                            has_dockerfile = True
                            logger.info("Found Dockerfile in subdirectory: %s", candidate)
                            break

                buildpack = getattr(service, 'buildpack', 'NIXPACKS')
                should_use_docker = (buildpack == 'DOCKER') and has_dockerfile

                if should_use_docker:
                    _append_log(deployment, f"\nDockerfile detected (Strategy: {buildpack}) — building image {local_tag} via Docker...\n")
                    logger.info("Building image with Docker: %s", local_tag)

                    dockerfile_arg_names = _extract_dockerfile_arg_names(dockerfile_path)
                    build_args = []
                    if dockerfile_arg_names:
                        arg_keys = [k for k in sorted(dockerfile_arg_names) if k in build_env_vars]
                        for k in arg_keys:
                            build_args.extend(["--build-arg", f"{k}={build_env_vars[k]}"])

                        arg_preview = ", ".join(arg_keys[:15])
                        _append_log(deployment, f"Using Dockerfile ARG build-args: {arg_preview or '(none)'}\n")
                    else:
                        for k, v in build_env_vars.items():
                            if k.startswith(("NEXT_PUBLIC_", "PUBLIC_", "VITE_")):
                                build_args.extend(["--build-arg", f"{k}={v}"])

                    dockerignore_path = os.path.join(build_context_dir, ".dockerignore")
                    if not os.path.exists(dockerignore_path):
                        try:
                            with open(dockerignore_path, "w", encoding="utf-8") as f:
                                f.write(".git\nnode_modules\nvenv\n__pycache__\n*.log\n")
                        except Exception: pass

                    import subprocess
                    docker_cmd = [
                        "docker", "build",
                        "-t", local_tag,
                        "-f", dockerfile_path,
                        "--cache-from", local_tag,
                        *build_args,
                        build_context_dir,
                    ]

                    try:
                        build_env = os.environ.copy()
                        build_env["DOCKER_BUILDKIT"] = "0"

                        process = subprocess.run(
                            docker_cmd, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=600, env=build_env,
                        )
                        build_result = {
                            "image_name": local_tag,
                            "stdout": _redact_values(process.stdout or "", secret_values),
                            "stderr": _redact_values(process.stderr or "", secret_values),
                        }
                    except subprocess.CalledProcessError as e:
                        full_error = str(e) + (getattr(e, "stdout", "") or "") + (getattr(e, "stderr", "") or "")
                        if is_buildkit_cache_error(full_error):
                            prune_buildkit_cache()

                        stdout = _redact_values(getattr(e, "stdout", "") or "", secret_values)
                        stderr = _redact_values(getattr(e, "stderr", "") or "", secret_values)
                        error_detail = ""
                        if stdout: error_detail += f"\n--- Build Output ---\n{stdout[-3000:]}"
                        if stderr: error_detail += f"\n--- Build Errors ---\n{stderr[-3000:]}"
                        raise RuntimeError(f"Docker build failed:{error_detail}") from e

                else:
                    reason = "Strategy: " + buildpack
                    if buildpack == 'DOCKER' and not has_dockerfile: reason += " (Dockerfile missing, fallback)"

                    _append_log(deployment, f"\nBuilding image {local_tag} via Nixpacks ({reason})...\n")
                    logger.info("Building image with Nixpacks: %s", local_tag)

                    build_result = NixpacksBuilder.build_image(
                        source_dir=build_context_dir,
                        image_name=local_tag,
                        env_vars=build_env_vars
                    )

                build_stdout = (_redact_values(build_result.get("stdout", ""), secret_values) if isinstance(build_result, dict) else "")
                build_stderr = (_redact_values(build_result.get("stderr", ""), secret_values) if isinstance(build_result, dict) else "")

                if build_stdout or build_stderr:
                    output = ""
                    if build_stdout: output += f"\n--- Build Output ---\n{build_stdout[-3000:]}\n"
                    if build_stderr: output += f"\n--- Build Errors ---\n{build_stderr[-3000:]}\n"
                    _append_log(deployment, output)

                _append_log(deployment, f"✓ Successfully built {local_tag}\n")
                _update_stage(deployment, 'Build', 'success', (timezone.now() - build_start).total_seconds())

                # C. Push to Registry (if configured)
                registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
                if registry_url:
                    _update_stage(deployment, 'Push', 'running')
                    push_start = timezone.now()

                    _append_log(deployment, f"\nPushing to {registry_url}...\n")
                    remote_tag = NixpacksBuilder.push_image(local_tag, registry_url)
                    image_name = remote_tag

                    _append_log(deployment, f"✓ Pushed to {remote_tag}\n")
                    _update_stage(deployment, 'Push', 'success', (timezone.now() - push_start).total_seconds())
                else:
                    image_name = local_tag

            except Exception as e:
                error_msg = f"Build pipeline failed: {str(e)}"
                logger.error(error_msg)

                deployment.refresh_from_db(fields=['build_logs', 'status'])
                # Only mark as FAILED if not already CANCELLED
                if deployment.status != Deployment.Status.CANCELLED:
                    deployment.status = Deployment.Status.FAILED
                    deployment.finished_at = timezone.now()
                    deployment.build_logs += f"\n✗ {error_msg}\n"
                    deployment.save(update_fields=['status', 'finished_at', 'build_logs'])
                    _broadcast_status(deployment)

                _broadcast_log(deployment, f"\n✗ {error_msg}\n")

                stages = deployment.pipeline_stages or []
                for stage in stages:
                    if stage.get('status') == 'running':
                        _update_stage(deployment, stage['name'], 'failed')

                if analyze_failure_task:
                    try:
                        analyze_failure_task.delay(str(deployment.id))
                        _append_log(deployment, "\n🤖 AI diagnosis requested...\n")
                    except Exception: pass

                if source_dir and os.path.exists(source_dir):
                    shutil.rmtree(source_dir, ignore_errors=True)

                raise self.retry(exc=e, countdown=30)

        # Step 2: Deploy
        _update_stage(deployment, 'Deploy', 'running')
        deploy_start = timezone.now()

        # Check cancellation
        deployment.refresh_from_db(fields=['status'])
        if deployment.status == Deployment.Status.CANCELLED:
            logger.info("Deployment %s cancelled before deploy", deployment_id)
            return

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save(update_fields=['status'])
        _broadcast_status(deployment)

        _append_log(deployment, "\nDeploying container...\n")
        compute = ComputeService(provider)
        env_vars = {env.key: env.value for env in service.env_vars.all()}
        if 'PUBLIC_DOMAIN' not in env_vars and service.public_domain: env_vars['PUBLIC_DOMAIN'] = service.public_domain
        if 'PORT' not in env_vars: env_vars['PORT'] = '8000'

        requested_replicas = service.min_replicas
        try: replicas = int(requested_replicas)
        except (TypeError, ValueError): replicas = 1
        if replicas < 1: replicas = 1

        from apps.deployments.models_storage import Volume
        db_volumes = Volume.objects.filter(service=service)
        volume_list = [{'name': v.name, 'mount_path': v.mount_path} for v in db_volumes]

        healthcheck_config = None
        if getattr(service, 'health_check_path', None):
            healthcheck_config = {
                'path': service.health_check_path,
                'interval': getattr(service, 'health_check_interval', 30),
                'timeout': getattr(service, 'health_check_timeout', 5),
                'retries': getattr(service, 'health_check_retries', 3),
            }
            service.health_status = 'starting'
            service.save(update_fields=['health_status'])

        resource = compute.deploy_container(
            name=service.name, image=image_name, env_vars=env_vars,
            cpu=int(service.cpu_cores * 1024), memory=service.memory_mb,
            replicas=replicas, volumes=volume_list, healthcheck=healthcheck_config,
            restart_policy=getattr(service, 'restart_policy', 'unless-stopped'),
        )

        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.container_id = resource.resource_id
        deployment.save(update_fields=['status', 'finished_at', 'container_id'])

        _update_stage(deployment, 'Deploy', 'success', (timezone.now() - deploy_start).total_seconds())

        log_line = (f"✓ Deployment successful! Container: {resource.resource_id[:12]}\n"
                    f"  Duration: {deployment.duration_seconds:.1f}s\n")
        _append_log(deployment, log_line)
        _broadcast_status(deployment)

        logger.info("Deployment %s successful on %s", deployment_id, provider.name)

        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)

    except Exception as e:
        logger.error("Deployment %s failed: %s", deployment_id, e)
        if deployment is not None:
            deployment.refresh_from_db(fields=['status', 'build_logs'])
            if deployment.status != Deployment.Status.CANCELLED:
                deployment.status = Deployment.Status.FAILED
                deployment.finished_at = timezone.now()
                deployment.build_logs += f"\n✗ Deployment failed: {str(e)}\n"
                deployment.save(update_fields=['status', 'finished_at', 'build_logs'])
                _broadcast_status(deployment)

            _broadcast_log(deployment, f"\n✗ Deployment failed: {str(e)}\n")

            if analyze_failure_task:
                try:
                    analyze_failure_task.delay(str(deployment.id))
                    _append_log(deployment, "\n🤖 AI diagnosis requested...\n")
                except Exception: pass

        if source_dir and os.path.exists(source_dir):
            shutil.rmtree(source_dir, ignore_errors=True)

        _update_stage(deployment, 'Deploy', 'failed')

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
