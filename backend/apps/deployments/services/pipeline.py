"""
Pipeline Manager Service.

Handles the build pipeline steps: Clone -> Analyze -> Build -> Push.
Refactored from monolithic tasks.py to improve maintainability and error isolation.
"""
import logging
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

import git
from django.conf import settings
from django.utils import timezone

from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.models import Deployment, EnvironmentVariable
from apps.deployments.services.git_manager import GitManager
from apps.deployments.utils import (
    append_log,
    update_stage,
    get_github_oauth_token_for_user,
    get_default_env_value,
    extract_dockerfile_arg_names,
    redact_values
)
from services.builders import is_buildkit_cache_error, prune_buildkit_cache

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class PipelineError(Exception):
    """Base class for pipeline failures."""


# pylint: disable=too-few-public-methods
class BuildError(PipelineError):
    """Raised when the build step fails (user error typically)."""


# pylint: disable=too-few-public-methods
class InfraError(PipelineError):
    """Raised when system infrastructure fails."""


class PipelineManager:
    """
    Orchestrates the CI/CD pipeline for a deployment.
    """

    def __init__(self, deployment: Deployment):
        self.deployment = deployment
        self.service = deployment.service
        self.build_dir = None
        self.source_dir = None
        self.image_name = None
        self.secret_values = []

    def run(self) -> str:
        """
        Executes the full pipeline.
        Returns: The final image tag/url.
        """
        try:
            self._setup()
            self._clone_repo()
            self._run_ai_analysis()
            self._inject_env_vars()
            self._auto_provision_addons()
            self._build_image()
            self._push_image()
            return self.image_name
        except PipelineError as e:
            # Re-raise known errors
            raise e
        except Exception as e:
            # Wrap unknown errors
            raise InfraError(f"Unexpected pipeline failure: {str(e)}") from e
        finally:
            self._cleanup()

    def _setup(self):
        """Initialize build environment."""
        self.build_dir = tempfile.mkdtemp(prefix=f"build_{self.deployment.id}_")
        self.deployment.pipeline_stages = []
        update_stage(self.deployment, 'Clone', 'pending')
        update_stage(self.deployment, 'Build', 'pending')
        if getattr(settings, 'CONTAINER_REGISTRY_URL', None):
            update_stage(self.deployment, 'Push', 'pending')

        # Load secrets for redaction
        env_vars = self.service.env_vars.all()
        self.secret_values = [
            env.value for env in env_vars
            if getattr(env, "is_secret", False) or re.search(
                r"(SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|API_KEY)",
                str(getattr(env, "key", "") or ""),
                re.IGNORECASE,
            )
        ]

    def _check_cancellation(self, stage_name: str):
        """Check if user cancelled deployment."""
        self.deployment.refresh_from_db(fields=['status'])
        if self.deployment.status == Deployment.Status.CANCELLED:
            raise PipelineError(f"Deployment cancelled during {stage_name}")

    def _clone_repo(self):
        """Step 1: Clone Repository."""
        update_stage(self.deployment, 'Clone', 'running')
        start_time = timezone.now()

        try:
            append_log(self.deployment, f"Cloning {self.service.repository_url}...\n")

            repo_token = None
            try:
                parsed = urlparse(self.service.repository_url or "")
                if (parsed.scheme in ("http", "https") and
                        (parsed.hostname or "").lower().endswith("github.com")):
                    repo_token = get_github_oauth_token_for_user(
                        getattr(self.service, "owner", None)
                    )
                    if repo_token:
                        append_log(
                            self.deployment,
                            "Using linked GitHub account for private repo access...\n"
                        )
            except Exception: # pylint: disable=broad-exception-caught
                pass

            self.source_dir = GitManager.clone_repo(
                repo_url=self.service.repository_url,
                branch=self.service.branch or 'main',
                destination=self.build_dir,
                token=repo_token,
            )

            # Metadata
            # pylint: disable=no-member
            repo = git.Repo(self.source_dir)
            self.deployment.commit_hash = repo.head.commit.hexsha
            self.deployment.commit_message = repo.head.commit.message
            self.deployment.save(update_fields=['commit_hash', 'commit_message'])

            update_stage(
                self.deployment, 'Clone', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(
                self.deployment,
                f"✓ Cloned successfully. Commit: {self.deployment.commit_hash[:7]}\n"
            )

        except Exception as e:
            update_stage(self.deployment, 'Clone', 'failed')
            raise BuildError(f"Clone failed: {str(e)}") from e

    def _run_ai_analysis(self):
        """Step 1.5: AI Analysis (Non-blocking)."""
        try:
            # pylint: disable=import-outside-toplevel
            from apps.intelligence.providers import ask_with_fallback
            from apps.intelligence.scanner import RepoScanner

            scanner = RepoScanner(self.source_dir)
            ai_context = scanner.build_ai_context()

            prompt = (
                f"Analyze this repo for deployment.\n"
                f"Service: {self.service.name}\n"
                f"Stack Context:\n{ai_context}\n"
                f"Identify potential deployment issues concisely."
            )
            response, provider = ask_with_fallback(prompt)

            self.deployment.ai_diagnosis = response
            self.deployment.save(update_fields=['ai_diagnosis'])
            append_log(self.deployment, f"\n🤖 AI Analysis ({provider}):\n{response}\n")

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("AI analysis failed: %s", e)
            append_log(self.deployment, "\n🤖 AI analysis skipped.\n")

    def _inject_env_vars(self):
        """Step 1.6: Auto-inject env vars."""
        try:
            # pylint: disable=import-outside-toplevel
            from apps.intelligence.scanner import RepoScanner
            scanner = RepoScanner(self.source_dir)
            scan_result = scanner.scan()

            detected = scan_result.get('env_vars', [])
            injected_count = 0

            for key in detected:
                default_val, should_inject = get_default_env_value(
                    key, scan_result, self.service.name
                )
                if should_inject:
                    _, created = EnvironmentVariable.objects.get_or_create(
                        service=self.service, key=key,
                        defaults={'value': default_val, 'is_secret': True}
                    )
                    if created:
                        injected_count += 1

            if injected_count:
                append_log(self.deployment, f"\n🔧 Auto-injected {injected_count} env vars.\n")

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Env injection failed: %s", e)

    # Dependency package → addon type mapping
    _REQUIREMENTS_ADDON_MAP = {
        # PostgreSQL
        'psycopg2': 'POSTGRES', 'psycopg2-binary': 'POSTGRES',
        'asyncpg': 'POSTGRES', 'django': 'POSTGRES',
        'dj-database-url': 'POSTGRES', 'sqlalchemy': 'POSTGRES',
        # Redis
        'redis': 'REDIS', 'celery': 'REDIS', 'django-redis': 'REDIS',
        'aioredis': 'REDIS', 'rq': 'REDIS',
        # MongoDB
        'pymongo': 'MONGODB', 'motor': 'MONGODB', 'mongoengine': 'MONGODB',
        # Qdrant
        'qdrant-client': 'QDRANT',
        # MySQL
        'mysqlclient': 'MYSQL', 'pymysql': 'MYSQL', 'aiomysql': 'MYSQL',
    }

    # Docker image prefix → addon type mapping
    _COMPOSE_ADDON_MAP = {
        'postgres': 'POSTGRES', 'redis': 'REDIS', 'mongo': 'MONGODB',
        'mysql': 'MYSQL', 'mariadb': 'MYSQL', 'qdrant': 'QDRANT',
        'elasticsearch': 'ELASTICSEARCH', 'rabbitmq': 'RABBITMQ',
        'memcached': 'MEMCACHED', 'clickhouse': 'CLICKHOUSE',
        'minio': 'MINIO',
    }

    def _auto_provision_addons(self):
        """Step 1.7: Auto-detect and provision required addons."""
        try:
            detected_types = set()

            # --- Strategy A: scan requirements.txt / Pipfile ---
            for name in ('requirements.txt', 'requirements/base.txt',
                         'requirements/production.txt'):
                req_path = os.path.join(self.source_dir, name)
                if os.path.isfile(req_path):
                    with open(req_path, 'r', encoding='utf-8',
                              errors='ignore') as f:
                        for line in f:
                            pkg = line.strip().split('==')[0].split('>=')[0] \
                                .split('<=')[0].split('[')[0].split('#')[0] \
                                .strip().lower()
                            addon = self._REQUIREMENTS_ADDON_MAP.get(pkg)
                            if addon:
                                detected_types.add(addon)

            # Also check pyproject.toml dependencies
            pyproject = os.path.join(self.source_dir, 'pyproject.toml')
            if os.path.isfile(pyproject):
                with open(pyproject, 'r', encoding='utf-8',
                          errors='ignore') as f:
                    content = f.read()
                    for pkg, addon in self._REQUIREMENTS_ADDON_MAP.items():
                        if pkg in content:
                            detected_types.add(addon)

            # Check package.json for Node.js apps
            pkg_json = os.path.join(self.source_dir, 'package.json')
            if os.path.isfile(pkg_json):
                import json
                try:
                    with open(pkg_json, 'r', encoding='utf-8') as f:
                        pkg_data = json.load(f)
                    all_deps = {}
                    all_deps.update(pkg_data.get('dependencies', {}))
                    all_deps.update(pkg_data.get('devDependencies', {}))
                    node_map = {
                        'pg': 'POSTGRES', 'sequelize': 'POSTGRES',
                        'typeorm': 'POSTGRES', 'prisma': 'POSTGRES',
                        'redis': 'REDIS', 'ioredis': 'REDIS',
                        'bullmq': 'REDIS', 'bull': 'REDIS',
                        'mongoose': 'MONGODB', 'mongodb': 'MONGODB',
                        'mysql2': 'MYSQL',
                        '@qdrant/js-client-rest': 'QDRANT',
                    }
                    for dep in all_deps:
                        addon = node_map.get(dep)
                        if addon:
                            detected_types.add(addon)
                except (json.JSONDecodeError, KeyError):
                    pass

            # --- Strategy B: scan docker-compose.yml ---
            for name in ('docker-compose.yml', 'docker-compose.yaml',
                         'compose.yml', 'compose.yaml'):
                compose_path = os.path.join(self.source_dir, name)
                if os.path.isfile(compose_path):
                    with open(compose_path, 'r', encoding='utf-8',
                              errors='ignore') as f:
                        content = f.read()
                    # Match image: lines
                    for match in re.findall(
                        r'image:\s*[\'"]?([^\s\'"]+)', content
                    ):
                        img = match.lower().split('/')[  # handle org/image
                            -1].split(':')[0]  # strip tag
                        addon = self._COMPOSE_ADDON_MAP.get(img)
                        if addon:
                            detected_types.add(addon)

            if not detected_types:
                return

            # --- Provision missing addons ---
            # pylint: disable=import-outside-toplevel
            from apps.deployments.models_addons import Addon
            from services.addon_provisioner import addon_provisioner

            existing = set(
                Addon.objects.filter(
                    service=self.service,
                    status__in=['ACTIVE', 'PROVISIONING']
                ).values_list('addon_type', flat=True)
            )

            to_provision = detected_types - existing
            if not to_provision:
                append_log(
                    self.deployment,
                    f"\n✅ All {len(detected_types)} detected addons "
                    f"already provisioned.\n"
                )
                return

            append_log(
                self.deployment,
                f"\n🔍 Auto-detected addons: "
                f"{', '.join(sorted(detected_types))}\n"
                f"📦 Provisioning {len(to_provision)} new: "
                f"{', '.join(sorted(to_provision))}\n"
            )

            for addon_type in to_provision:
                addon = Addon.objects.create(
                    service=self.service,
                    name=f"{addon_type.lower()}-{self.service.name}"[:255],
                    addon_type=addon_type,
                    status=Addon.Status.PROVISIONING,
                )
                try:
                    _, url = addon_provisioner.provision(addon)
                    addon.connection_url = url
                    addon.status = Addon.Status.ACTIVE
                    addon.save()

                    # Inject connection URL as env var
                    env_key = addon_provisioner.ENV_KEY_MAP.get(
                        addon_type, f"{addon_type}_URL"
                    )
                    EnvironmentVariable.objects.update_or_create(
                        service=self.service, key=env_key,
                        defaults={'value': url, 'is_secret': True}
                    )

                    # Qdrant: also set QDRANT_HOST/QDRANT_PORT
                    if addon_type == 'QDRANT':
                        from urllib.parse import urlparse
                        parsed = urlparse(url)
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key='QDRANT_HOST',
                            defaults={
                                'value': parsed.hostname or 'localhost',
                                'is_secret': False
                            }
                        )
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key='QDRANT_PORT',
                            defaults={
                                'value': str(parsed.port or 6333),
                                'is_secret': False
                            }
                        )

                    append_log(
                        self.deployment,
                        f"  ✅ {addon_type} provisioned → {env_key}\n"
                    )

                except Exception as e:  # pylint: disable=broad-exception-caught
                    addon.status = Addon.Status.FAILED
                    addon.save()
                    append_log(
                        self.deployment,
                        f"  ⚠️ {addon_type} provisioning failed: {e}\n"
                    )
                    logger.warning(
                        "Auto-provision %s failed: %s", addon_type, e
                    )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Auto-addon provisioning failed: %s", e)

    def _build_image(self):
        """Step 2: Build Image."""
        update_stage(self.deployment, 'Build', 'running')
        start_time = timezone.now()
        self._check_cancellation('Build')

        try:
            tag_hash = self.deployment.commit_hash[:7]
            self.image_name = f"smsly/{self.service.name.lower()}:{tag_hash}"

            # Determine build context (root dir)
            context_dir = self._get_build_context()

            # Dockerfile detection
            dockerfile_path = self._find_dockerfile(context_dir)
            use_docker = (self.service.buildpack == 'DOCKER' and dockerfile_path)

            if use_docker:
                self._build_with_docker(context_dir, dockerfile_path)
            else:
                self._build_with_nixpacks(context_dir)

            update_stage(
                self.deployment, 'Build', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(self.deployment, f"✓ Build successful: {self.image_name}\n")

        except Exception as e:
            update_stage(self.deployment, 'Build', 'failed')
            raise BuildError(f"Build failed: {str(e)}") from e

    def _get_build_context(self) -> str:
        """Resolve root directory."""
        root_dir = (self.service.root_directory or "/").strip()
        if root_dir in ("", "/", ".", "./"):
            return self.source_dir

        candidate = os.path.abspath(os.path.join(self.source_dir, root_dir.lstrip("/\\")))
        if not candidate.startswith(os.path.abspath(self.source_dir)):
            raise BuildError("root_directory must be inside the repo")
        if not os.path.isdir(candidate):
            raise BuildError(f"Directory not found: {root_dir}")
        return candidate

    def _find_dockerfile(self, context_dir: str) -> str:
        """Locate Dockerfile in context or subdirs."""
        # Direct check
        direct = os.path.join(context_dir, "Dockerfile")
        if os.path.isfile(direct):
            return direct

        # Shallow scan
        for entry in os.listdir(context_dir):
            candidate = os.path.join(context_dir, entry, "Dockerfile")
            if os.path.isdir(os.path.join(context_dir, entry)) and os.path.isfile(candidate):
                return candidate
        return None

    def _build_with_docker(self, context_dir: str, dockerfile_path: str):
        """Execute Docker build."""
        append_log(
            self.deployment,
            f"Building with Docker ({os.path.basename(dockerfile_path)})...\n"
        )

        build_args = []
        env_map = {env.key: env.value for env in self.service.env_vars.all()}

        # Smart arg detection
        defined_args = extract_dockerfile_arg_names(dockerfile_path)
        if defined_args:
            for k in defined_args:
                if k in env_map:
                    build_args.extend(["--build-arg", f"{k}={env_map[k]}"])
        else:
            # Fallback: pass frontend-like vars
            for k, v in env_map.items():
                if k.startswith(("NEXT_PUBLIC_", "VITE_", "PUBLIC_")):
                    build_args.extend(["--build-arg", f"{k}={v}"])

        cmd = [
            "docker", "build",
            "-t", self.image_name,
            "-f", dockerfile_path,
            "--cache-from", self.image_name,
            *build_args,
            context_dir
        ]

        self._run_subprocess(cmd, context_dir)

    def _build_with_nixpacks(self, context_dir: str):
        """Execute Nixpacks build."""
        append_log(self.deployment, "Building with Nixpacks...\n")
        env_map = {env.key: env.value for env in self.service.env_vars.all()}

        result = NixpacksBuilder.build_image(
            source_dir=context_dir,
            image_name=self.image_name,
            env_vars=env_map
        )

        # NixpacksBuilder returns dict with stdout/stderr
        if result.get("stderr"):
            append_log(self.deployment, f"[Nixpacks Log]\n{result['stderr']}\n")

    def _run_subprocess(self, cmd: list, cwd: str):
        """Helper to run shell commands with logging."""
        env = os.environ.copy()
        env["DOCKER_BUILDKIT"] = "0"  # Disable buildkit if causing cache issues

        try:
            process = subprocess.run(
                cmd, check=True, cwd=cwd, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=7200  # 2 hours for heavy builds
            )
            # Log output (redacted)
            output = redact_values(process.stdout + process.stderr, self.secret_values)
            if len(output) > 5000:
                output = output[-5000:] + "\n...(truncated)"
            append_log(self.deployment, output)

        except subprocess.CalledProcessError as e:
            full_err = redact_values(e.stdout + e.stderr, self.secret_values)

            # Auto-prune cache check
            if is_buildkit_cache_error(full_err):
                prune_buildkit_cache()
                raise BuildError(
                    "Docker cache corruption detected. Cache pruned. Please retry."
                ) from e

            append_log(self.deployment, full_err)
            raise BuildError("Command failed") from e

    def _push_image(self):
        """Step 3: Push to Registry."""
        registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None)
        if not registry_url:
            return

        update_stage(self.deployment, 'Push', 'running')
        start_time = timezone.now()
        self._check_cancellation('Push')

        try:
            append_log(self.deployment, f"Pushing to {registry_url}...\n")
            remote_tag = NixpacksBuilder.push_image(self.image_name, registry_url)
            self.image_name = remote_tag

            update_stage(
                self.deployment, 'Push', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(self.deployment, f"✓ Pushed: {remote_tag}\n")

        except Exception as e:
            update_stage(self.deployment, 'Push', 'failed')
            raise SystemError(f"Registry push failed: {e}") from e

    def _cleanup(self):
        """Remove temp artifacts."""
        if self.build_dir and os.path.exists(self.build_dir):
            try:
                shutil.rmtree(self.build_dir)
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Failed to cleanup build dir %s: %s", self.build_dir, e)
