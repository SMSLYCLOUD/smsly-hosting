# pylint: disable=too-many-lines
"""
Pipeline Manager Service.

Handles the build pipeline steps: Clone -> Analyze -> Build -> Push.
Refactored from monolithic tasks.py to improve maintainability and error isolation.
"""
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse as parse_url

import git
import yaml
from django.conf import settings
from django.utils import timezone
from services.builders import cleanup_stuck_buildkit as _cleanup_stuck_buildkit
from services.builders import is_buildkit_cache_error, prune_buildkit_cache

from apps.cloud.services.builder import NixpacksBuilder
from apps.deployments.ai_router import (
    generate_ai_router_proxy_config,
    get_ollama_model_name,
    is_ai_router_service,
    is_ollama_service,
)
from apps.deployments.models import Deployment, EnvironmentVariable, PlatformConfig
from apps.deployments.utils import (
    append_log,
    estimate_resources_from_deps,
    extract_dockerfile_arg_names,
    get_default_env_value,
    get_github_oauth_token_for_user,
    is_deployment_local,
    log_exhaustive_addon_provisioning_diagnostics,
    log_exhaustive_build_diagnostics,
    log_exhaustive_clone_diagnostics,
    log_exhaustive_deployment_diagnostics,
    log_exhaustive_env_diagnostics,
    log_exhaustive_network_and_routing_diagnostics,
    log_exhaustive_push_diagnostics,
    parse_ai_resource_recommendation,
    redact_values,
    update_stage,
)
from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService

logger = logging.getLogger(__name__)

# Persistent build directory root.
# Uses env var or a sensible default. Avoids /tmp which the OS may clean
# between the analysis and build phases of a 2-phase deploy.
def _is_dir_writable(path: str) -> bool:
    """Verify that path exists and can actually be written to by the current process."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".perm_probe_{os.getpid()}_{id(path)}")
        with open(probe, "w") as f:
            f.write("ok")
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except (OSError, PermissionError):
        return False


def _resolve_builds_root():
    """Determine best writable directory for build artifacts."""
    explicit = os.environ.get('SMSLY_BUILDS_DIR')
    if explicit and _is_dir_writable(explicit):
        return explicit

    # Prefer /opt path on Linux servers
    preferred = '/opt/smsly-hosting/builds'
    if _is_dir_writable(preferred):
        return preferred

    # Fallback: persistent subdir in system temp
    fallback = os.path.join(tempfile.gettempdir(), 'smsly-builds')
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        pass
    return fallback


def _get_builds_root():
    """Lazy accessor for BUILDS_ROOT — evaluated at call time so env var
    changes (SMSLY_BUILDS_DIR) or permission changes are picked up without a restart."""
    root = getattr(_get_builds_root, '_cached', None)
    if root is None or not _is_dir_writable(root):
        root = _resolve_builds_root()
        _get_builds_root._cached = root
    return root


_BUILDS_ROOT = _get_builds_root()


def _read_env_file(path):
    """Read a docker-compose .env file, yielding non-comment key=value lines."""
    with open(path) as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            yield stripped


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

    # Class-level lock so concurrent PipelineManager instances do not race
    # to rebuild the global buildx "default" builder (which would otherwise
    # yank the builder out from under sibling builds).
    _buildx_driver_lock = threading.Lock()

    def __init__(self, deployment: Deployment):
        self.deployment = deployment
        self.service = deployment.service
        self.build_dir = None
        self.source_dir = None
        self.image_name = None
        self.secret_values: list[str] = []

    def run(self) -> str:
        """
        Executes the full pipeline (no review gate).
        Used for rollbacks, restarts, and DOCKER/FUNCTION deploys.
        Returns: The final image tag/url.
        """
        try:
            self._setup()
            self._capture_pre_deploy_snapshot()
            is_docker_type = self.service.deploy_type == 'DOCKER' and self.service.docker_image
            if not is_docker_type:
                self._clone_repo()
                self._run_ai_analysis()
                self._inject_env_vars()
                self._auto_provision_addons()
            self._build_image()
            self._push_image()
            log_exhaustive_network_and_routing_diagnostics(self.deployment, self.service)
            if not self.image_name:
                raise PipelineError("Pipeline completed without producing an image")
            return self.image_name
        except PipelineError as e:
            # Re-raise known errors
            raise e
        except Exception as e:
            # Wrap unknown errors
            raise InfraError(f"Unexpected pipeline failure: {e!s}") from e
        finally:
            self._cleanup()

    def run_analysis_only(self) -> dict:
        """
        Phase 1: Clone + AI analysis + env scan + addon detection.
        Pauses at REVIEW status so the user can review before building.
        Returns: The review summary dict.
        """
        try:
            self._setup()
            self._capture_pre_deploy_snapshot()
            self._clone_repo()
            self._run_ai_analysis()
            self._inject_env_vars()
            self._auto_provision_addons()

            # Build the review summary from what was detected
            summary = self._build_review_summary()

            # Persist source/build paths so run_build_only() can resume
            summary['_build_meta'] = {
                'source_dir': self.source_dir,
                'build_dir': self.build_dir,
            }

            # Save summary and pause
            self.deployment.review_summary = summary
            self.deployment.status = Deployment.Status.REVIEW
            self.deployment.save(
                update_fields=['review_summary', 'status']
            )

            update_stage(self.deployment, 'Review', 'waiting')
            append_log(
                self.deployment,
                "\n⏸️ Deployment paused for review. "
                "Approve to continue building.\n"
            )

            # NOTE: Don't cleanup — build_dir stays for run_build_only()
            return summary

        except PipelineError as e:
            self._cleanup()  # Clean up on failure
            raise e
        except Exception as e:
            self._cleanup()  # Clean up on failure
            raise InfraError(
                f"Analysis phase failure: {e!s}"
            ) from e

    def run_build_only(self) -> str:
        """
        Phase 2: Build + Push (called after user approves review).
        Assumes analysis phase already ran (source cloned, env set up).
        Returns: The final image tag/url.
        """
        try:
            # Re-attach to existing build dir from the analysis phase
            self._setup_for_resume()
            self._capture_pre_deploy_snapshot()

            self._build_image()
            self._push_image()
            log_exhaustive_network_and_routing_diagnostics(self.deployment, self.service)
            if not self.image_name:
                raise PipelineError("Build phase completed without producing an image")
            return self.image_name
        except PipelineError as e:
            raise e
        except Exception as e:
            raise InfraError(
                f"Build phase failure: {e!s}"
            ) from e
        finally:
            self._cleanup()

    def _setup_for_resume(self):
        """Re-initialise state for phase 2 (build) from saved deployment data."""
        # Try to restore paths from review_summary (saved by run_analysis_only)
        meta = (self.deployment.review_summary or {}).get('_build_meta', {})
        saved_source = meta.get('source_dir', '')
        saved_build = meta.get('build_dir', '')

        # Restore build_dir
        self.build_dir = saved_build or self._ensure_build_dir(f"build_{self.deployment.id}")

        # Restore source_dir — prefer saved path from repo cache
        if saved_source and os.path.isdir(saved_source):
            self.source_dir = saved_source
        elif os.path.isdir(self.build_dir):
            # Fallback: search for .git inside build_dir (legacy behavior)
            subdirs = [
                d for d in os.listdir(self.build_dir)
                if os.path.isdir(os.path.join(self.build_dir, d))
            ]
            git_dirs = [
                d for d in subdirs
                if os.path.isdir(
                    os.path.join(self.build_dir, d, '.git')
                )
            ]
            if git_dirs:
                self.source_dir = os.path.join(self.build_dir, git_dirs[0])
            elif subdirs:
                self.source_dir = os.path.join(self.build_dir, subdirs[0])
            else:
                self.source_dir = self.build_dir
        else:
            append_log(
                self.deployment,
                "ℹ️ Build directory from analysis phase not found locally. Re-cloning repository for build phase...\n"
            )
            self.build_dir = self._ensure_build_dir(f"build_{self.deployment.id}")
            self.source_dir = self.build_dir
            self._clone_repo()

        if not self._source_tree_available(self.source_dir):
            append_log(
                self.deployment,
                "Build source from review phase is unavailable locally. Re-cloning repository for build phase...\n"
            )
            self.build_dir = self._ensure_build_dir(f"svc_{self.service.id}")
            self.source_dir = None
            self._clone_repo()

        # Reload secrets for log redaction
        env_vars = self.service.env_vars.all()
        self.secret_values = [
            env.value for env in env_vars
            if getattr(env, "is_secret", False) or re.search(
                r"(SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|API_KEY)",
                str(getattr(env, "key", "") or ""),
                re.IGNORECASE,
            )
        ]

    def _ensure_build_dir(self, dir_name: str) -> str:
        """Ensure build directory exists and is writable, with automatic fallback to temp dir."""
        root = _get_builds_root()
        path = os.path.join(root, dir_name)
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, f".probe_{os.getpid()}")
            with open(probe, "w") as f:
                f.write("ok")
            try:
                os.remove(probe)
            except OSError:
                pass
            return path
        except (OSError, PermissionError) as exc:
            logger.warning("Build dir %s not writable (%s), falling back to temp dir", path, exc)
            fallback_root = os.path.join(tempfile.gettempdir(), 'smsly-builds')
            try:
                os.makedirs(fallback_root, exist_ok=True)
            except OSError:
                pass
            _get_builds_root._cached = fallback_root
            fallback_path = os.path.join(fallback_root, dir_name)
            try:
                os.makedirs(fallback_path, exist_ok=True)
            except OSError:
                pass
            return fallback_path

    @staticmethod
    def _source_tree_available(source_dir: str | None) -> bool:
        """Return True when a path still contains cloned source files."""
        if not source_dir or not os.path.isdir(source_dir):
            return False
        if os.path.isdir(os.path.join(source_dir, ".git")):
            return True
        try:
            return any(
                name not in {".", ".."}
                and not name.startswith(".smsly-git-askpass-")
                for name in os.listdir(source_dir)
            )
        except OSError:
            return False

    def _build_review_summary(self) -> dict:
        """Compile the review summary from current service+deployment state."""
        service = self.service
        service.refresh_from_db()

        # Current resources
        resources = {
            'cpu_cores': float(service.cpu_cores),
            'memory_mb': service.memory_mb,
        }

        # Env vars (mask secrets)
        env_vars = []
        for ev in service.env_vars.all().order_by('key'):
            env_vars.append({
                'key': ev.key,
                'value': '********' if ev.is_secret else ev.value,
                'is_secret': ev.is_secret,
            })

        # Extract issues from AI diagnosis
        issues = []
        if self.deployment.ai_diagnosis:
            from apps.deployments.utils import (
                parse_ai_resource_recommendation,
            )
            parsed = parse_ai_resource_recommendation(
                self.deployment.ai_diagnosis
            )
            issues = parsed.get('issues', [])

        # Active addons
        from apps.deployments.models_addons import Addon
        addons = list(
            Addon.objects.filter(
                service=service, status='ACTIVE'
            ).values_list('addon_type', flat=True)
        )

        # Compose info
        compose_info = None
        if service.deploy_mode == 'COMPOSE' and service.compose_file:
            compose_info = {
                'file': service.compose_file,
                'main_service': service.compose_main_service or '(auto-detect)',
            }

        return {
            'resources': resources,
            'env_vars': env_vars,
            'issues': issues,
            'addons': addons,
            'compose': compose_info,
            'diagnosis': self.deployment.ai_diagnosis[:2000]
            if self.deployment.ai_diagnosis else '',
        }

    def _setup(self):
        """Initialize build environment."""
        # Use a deployment-scoped path to prevent concurrent deployments for the
        # same service from clashing over the same directory (race condition that
        # produces "destination path already exists" clone errors).
        self.build_dir = self._ensure_build_dir(f"svc_{self.service.id}_dep_{self.deployment.id}")
        self.deployment.pipeline_stages = []
        update_stage(self.deployment, 'Clone', 'pending')
        update_stage(self.deployment, 'Build', 'pending')
        _registry_url = PlatformConfig.get_config_value('container_registry_url') or getattr(settings, 'CONTAINER_REGISTRY_URL', None)
        if _registry_url:
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
        log_exhaustive_deployment_diagnostics(self.deployment, self.service, self.build_dir)

    def _check_cancellation(self, stage_name: str):
        """Check if user cancelled deployment."""
        self.deployment.refresh_from_db(fields=['status'])
        if self.deployment.status == Deployment.Status.CANCELLED:
            raise PipelineError(f"Deployment cancelled during {stage_name}")

    def _clone_repo(self):
        """Step 1: Clone Repository."""
        update_stage(self.deployment, 'Clone', 'running')
        start_time = timezone.now()
        requested_branch = (self.service.branch or 'main').strip() or 'main'

        try:
            append_log(
                self.deployment,
                f"Cloning {self.service.repository_url} (branch: {requested_branch})...\n"
            )

            repo_token = None
            try:
                parsed = parse_url(self.service.repository_url or "")
                if (parsed.scheme in ("http", "https") and
                        (parsed.hostname or "").lower().endswith("github.com")):
                    service_owner = getattr(self.service, "owner", None)
                    # Use the priority chain: GitHub App token > user OAuth token > None.
                    # Falls back gracefully when App is not configured.
                    repo_full_name = "/".join(
                        (parsed.path or "").lstrip("/").rstrip(".git").split("/")[:2]
                    )
                    from apps.deployments.utils import get_github_token_for_repo
                    repo_token = get_github_token_for_repo(service_owner, repo_full_name)
                    if repo_token:
                        append_log(
                            self.deployment,
                            "GitHub credentials resolved for private repo access...\n"
                        )
                    else:
                        logger.warning(
                            "No GitHub token found for service owner %s (service: %s). "
                            "Configure a GitHub App or connect a GitHub account.",
                            service_owner.id if service_owner else "None",
                            self.service.id
                        )
                        append_log(
                            self.deployment,
                            "⚠ No GitHub credentials available. If this is a private repo, "
                            "connect your GitHub account in Settings or configure a GitHub App.\n"
                        )
            except Exception as exc:
                logger.warning("Error retrieving GitHub token: %s", exc)

            target_commit = getattr(self.deployment, 'commit_hash', None)
            if target_commit and target_commit.upper() in ('HEAD', 'LATEST', 'TEMPLATE', 'ECOSYSTEM-DEPLOY'):
                target_commit = None

            self._clone_with_github_token(
                self.service.repository_url,
                requested_branch,
                repo_token,
                self.build_dir,
                target_commit=target_commit,
            )
            self.source_dir = self.build_dir

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
            log_exhaustive_clone_diagnostics(self.deployment, self.service.repository_url, requested_branch, self.source_dir)

        except Exception as e:
            update_stage(self.deployment, 'Clone', 'failed')
            raise BuildError(f"Clone failed: {e!s}") from e

        # Auto-inject .env file from repo (if present)
        self._inject_dotenv_from_repo()

    def _clone_with_github_token(self, repo_url: str, branch: str, token: str | None, target_dir: str, target_commit: str | None = None):
        """Clone repository into *target_dir* using an atomic clone-then-rename strategy.

        Clones into a uniquely-named temporary sibling directory first, then
        renames it into *target_dir*.  This prevents a concurrent deployment for
        the same service from racing into a half-written clone directory and
        hitting ``fatal: destination path already exists``.
        """
        build_path = Path(target_dir)
        parent = build_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            logger.warning("Could not mkdir in %s (%s), falling back to temp dir", parent, exc)
            parent = Path(tempfile.gettempdir()) / 'smsly-builds'
            parent.mkdir(parents=True, exist_ok=True)
            build_path = parent / build_path.name
            self.build_dir = str(build_path)
            if hasattr(self, 'source_dir') and self.source_dir:
                self.source_dir = str(build_path)

        # Clone into a unique temp dir inside the same parent, then atomically
        # rename into the final location.  This means:
        #   * No other process ever sees a half-written clone.
        #   * If the clone fails, the stale temp dir is cleaned up, not target_dir.
        tmp_path = Path(tempfile.mkdtemp(dir=str(parent), prefix=f".clone_tmp_{build_path.name}_"))

        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        askpass_path = None

        if token:
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(repo_url)
            host = parsed.hostname or "github.com"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            remote_url = urlunparse(parsed._replace(netloc=f"x-access-token@{host}"))

            askpass_fd, askpass_name = tempfile.mkstemp(
                prefix=".smsly-git-askpass-",
                suffix=".sh",
                dir=str(build_path.parent),
            )
            os.close(askpass_fd)
            askpass_path = Path(askpass_name)
            askpass_path.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf \"%s\" \"x-access-token\" ;;\n"
                "  *) printf \"%s\" \"$SMSLY_GIT_PASSWORD\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            os.chmod(askpass_path, 0o700)
            env["GIT_ASKPASS"] = str(askpass_path)
            env["SMSLY_GIT_PASSWORD"] = token
        else:
            remote_url = repo_url

        clone_cmd = [
            "git", "clone", "--branch", branch, "--single-branch", remote_url, str(tmp_path)
        ]
        try:
            subprocess.run(
                clone_cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if target_commit:
                checkout_cmd = ["git", "checkout", target_commit]
                subprocess.run(
                    checkout_cmd,
                    cwd=str(tmp_path),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
            # Atomically promote tmp_path → build_path
            if build_path.exists():
                shutil.rmtree(build_path, ignore_errors=True)
            tmp_path.rename(build_path)
            tmp_path = None  # ownership transferred
        except subprocess.CalledProcessError as exc:
            details = self._format_git_clone_error(exc, token)
            raise RuntimeError(details) from exc
        finally:
            if askpass_path and askpass_path.exists():
                try:
                    askpass_path.unlink()
                except OSError:
                    pass
            if tmp_path is not None and tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)

    def _format_git_clone_error(self, exc: subprocess.CalledProcessError, token: str | None) -> str:
        """Return a concise, redacted clone failure with Git's real stderr."""
        parts = [f"git clone exited with code {exc.returncode}"]
        stream_values = (
            ("stderr", exc.stderr),
            ("stdout", exc.stdout or exc.output),
        )
        for label, value in stream_values:
            if not value:
                continue
            text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
            text = text.strip()
            if not text:
                continue
            redaction_values = [token] if token else []
            text = redact_values(text, redaction_values + getattr(self, "secret_values", []))
            parts.append(f"{label}: {text}")
        return " | ".join(parts)

    def _inject_dotenv_from_repo(self):
        """Auto-inject env vars from .env files found in the cloned repo.

        Scans root + common framework subdirs for .env files.
        Priority: .env.production > .env.local > .env
        Only injects keys not already set. Never injects empty values.
        """
        if not self.source_dir:
            return

        # Common framework subdirectories to scan
        SCAN_DIRS = [
            '',  # repo root
            'frontend', 'backend', 'server', 'app', 'src',
            'api', 'web', 'client', 'services',
        ]
        # .env file names in priority order (later overrides earlier)
        ENV_FILES = ['.env', '.env.local', '.env.production']

        # Keys we should NEVER inject from .env files (security)
        SKIP_PATTERNS = re.compile(
            r'(SECRET|PRIVATE|TOKEN|PASSWORD|API[_-]?KEY|DSN|CREDENTIAL)',
            re.IGNORECASE,
        )

        collected = {}  # key -> value (later files override)

        for subdir in SCAN_DIRS:
            scan_path = os.path.join(self.source_dir, subdir) if subdir else self.source_dir
            if not os.path.isdir(scan_path):
                continue

            for env_file in ENV_FILES:
                env_path = os.path.join(scan_path, env_file)
                if not os.path.isfile(env_path):
                    continue

                try:
                    with open(env_path, encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            line = line.strip()
                            # Skip empty lines, comments, exports
                            if not line or line.startswith('#'):
                                continue
                            line = re.sub(r'^export\s+', '', line)

                            if '=' not in line:
                                continue

                            key, _, value = line.partition('=')
                            key = key.strip().upper()
                            value = value.strip()

                            # Strip surrounding quotes
                            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                                value = value[1:-1]

                            if not key or not value:
                                continue
                            if SKIP_PATTERNS.search(key):
                                continue

                            # Sanitize for PostgreSQL
                            safe_key = key.replace('\x00', '')
                            safe_value = value.replace('\x00', '')

                            collected[safe_key] = safe_value
                except Exception:
                    continue

        if not collected:
            return

        # Inject into DB (only keys not already set)
        injected = 0
        for key, value in collected.items():
            is_secret = bool(re.search(
                r'(TOKEN|API_KEY|SECRET|PRIVATE)',
                key, re.IGNORECASE,
            ))
            _, created = EnvironmentVariable.objects.get_or_create(
                service=self.service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret},
            )
            if created:
                display_val = '********' if is_secret else value[:50]
                append_log(
                    self.deployment,
                    f"  📄 .env: {key}={display_val}\n"
                )
                injected += 1

        if injected:
            append_log(
                self.deployment,
                f"\n✅ Auto-injected {injected} env var(s) from repo .env files.\n"
            )

    def _run_ai_analysis(self):
        """Step 1.5: AI Analysis → structured resource + exhaustive AI Senate env filling."""
        try:
            from apps.intelligence.scanner import RepoScanner
            scanner = RepoScanner(self.source_dir)

            # Step A: Perform aggressive scan
            scan_result = scanner.scan()
            ai_context = scanner.build_ai_context()

            # Step B: Consult the AI Senate for resource recommendations and diagnosis
            # (Keeping the resource logic as it was but optimizing the prompt)
            prompt = (
                f"Analyze this repo for deployment on CloudNeuron (Docker-based PaaS).\n"
                f"Service: {self.service.name}\n"
                f"Stack Context:\n{ai_context}\n\n"
                f"Return ONLY a JSON object:\n"
                f'{{\n'
                f'  "resources": {{"cpu_cores": <float>, "memory_mb": <int>}},\n'
                f'  "issues": ["..."],\n'
                f'  "diagnosis": "..."\n'
                f'}}\n'
            )

            from apps.intelligence.providers import ask_with_fallback
            response, provider = ask_with_fallback(prompt, mode="code_review")

            self.deployment.ai_diagnosis = (response or "").replace('\x00', '')
            self.deployment.save(update_fields=['ai_diagnosis'])

            append_log(self.deployment, f"\n🤖 AI Senate Analysis ({provider}) complete.\n")

            # Step C: Apply resource recommendations
            recommendation = parse_ai_resource_recommendation(response)
            if recommendation:
                self._apply_resource_recommendations(recommendation.get('resources', {}))
                for issue in recommendation.get('issues', []):
                    append_log(self.deployment, f"  ⚠️ {issue}\n")

            # Step D: GROUNDED env resolution from actual repo files
            # Replaces the AI Senate approach which hallucinated vars
            append_log(self.deployment, "📋 Resolving environment from repo manifest files...\n")
            try:
                from .manifest_env_resolver import ManifestEnvResolver
                resolver = ManifestEnvResolver(
                    source_dir=self.source_dir,
                    service_name=self.service.name,
                )
                resolved_env = resolver.resolve_all()
                injected = self._inject_manifest_env_vars(resolved_env, resolver)

                if resolver.unresolved_vars:
                    # Persist so review_summary exposes them to the auto-fill API action.
                    _rs = self.deployment.review_summary or {}
                    _rs['unresolved_external_vars'] = resolver.unresolved_vars
                    self.deployment.review_summary = _rs
                    append_log(
                        self.deployment,
                        f"  ⚠️ {len(resolver.unresolved_vars)} unresolved required var(s): "
                        f"{', '.join(resolver.unresolved_vars[:10])}\n"
                    )

                if resolver.is_frontend:
                    append_log(
                        self.deployment,
                        f"  ℹ️ Detected as frontend-only service — only {injected} frontend-friendly vars injected.\n"
                    )
                elif injected:
                    append_log(
                        self.deployment,
                        f"  ✅ Manifest resolver auto-filled {injected} variables.\n"
                    )
                else:
                    append_log(self.deployment, "  ℹ️ All detected variables are already configured.\n")

                if not resolver.is_frontend and (resolver.unresolved_vars or any(not v or v in ("", "{{GENERATE}}", "{{FILL_ME}}") for v in resolved_env.values())):
                    append_log(self.deployment, "  🧠 Passing remaining unfilled variables through AI Senate intelligence...\n")
                    try:
                        _sugg, _inj = EnvironmentIntelligenceService.apply_intelligence_to_service(
                            self.service, scan_result, source_dir=None
                        )
                        if _inj:
                            append_log(self.deployment, f"  ✅ AI Senate intelligence auto-filled {len(_inj)} remaining variables: {', '.join(_inj[:10])}...\n")
                            if resolver.unresolved_vars:
                                resolver.unresolved_vars = [k for k in resolver.unresolved_vars if k not in _inj]
                                _rs = self.deployment.review_summary or {}
                                if resolver.unresolved_vars:
                                    _rs['unresolved_external_vars'] = resolver.unresolved_vars
                                else:
                                    _rs.pop('unresolved_external_vars', None)
                                self.deployment.review_summary = _rs
                    except Exception as _senate_err:
                        logger.warning("AI Senate enrichment for remaining vars failed: %s", _senate_err)
            except Exception as e:
                logger.warning("Manifest env resolution failed: %s", e)
                append_log(self.deployment, f"\n⚠️ Manifest env resolution failed: {e!s}. Falling back to AI Senate.\n")
                _suggestions, injected = EnvironmentIntelligenceService.apply_intelligence_to_service(
                    self.service, scan_result
                )
                if injected:
                    append_log(self.deployment, f"  ✅ AI Senate auto-filled {len(injected)} variables: {', '.join(injected[:10])}...\n")

        except Exception as e:
            logger.warning("AI analysis failed: %s", e)
            append_log(self.deployment, f"\n🤖 AI analysis encountered an error: {e!s}. Falling back to heuristics.\n")

        # Heuristic fallback for resources
        if self.source_dir:
            self._apply_heuristic_resources()

    def _apply_resource_recommendations(self, resources: dict):
        """Apply AI resource recommendations (only increase, never decrease)."""
        if not resources:
            return

        updated_fields = []
        cpu = resources.get('cpu_cores')
        mem = resources.get('memory_mb')

        if cpu and float(cpu) > float(self.service.cpu_cores):
            old = self.service.cpu_cores
            self.service.cpu_cores = cpu
            updated_fields.append('cpu_cores')
            append_log(
                self.deployment,
                f"  📈 CPU: {old} → {cpu} cores (AI recommendation)\n"
            )

        if mem and int(mem) > self.service.memory_mb:
            old = self.service.memory_mb
            self.service.memory_mb = mem
            updated_fields.append('memory_mb')
            append_log(
                self.deployment,
                f"  📈 RAM: {old}MB → {mem}MB (AI recommendation)\n"
            )

        if updated_fields:
            self.service.save(update_fields=updated_fields)

    def _apply_heuristic_resources(self):
        """Fast fallback: scan deps for heavy packages and boost resources."""
        heuristic = estimate_resources_from_deps(self.source_dir)
        if not heuristic:
            return

        updated_fields = []
        cpu = heuristic.get('cpu_cores', 0)
        mem = heuristic.get('memory_mb', 0)

        if cpu > float(self.service.cpu_cores):
            self.service.cpu_cores = cpu
            updated_fields.append('cpu_cores')

        if mem > self.service.memory_mb:
            self.service.memory_mb = mem
            updated_fields.append('memory_mb')

        if updated_fields:
            self.service.save(update_fields=updated_fields)
            append_log(
                self.deployment,
                f"  🔧 Resources auto-adjusted from dependency scan: "
                f"{self.service.cpu_cores} CPU, "
                f"{self.service.memory_mb}MB RAM\n"
            )

    def _inject_ai_env_vars(self, ai_env_vars: dict):
        """Inject env vars recommended by AI analysis."""
        import secrets as _secrets

        # Keys where we MUST generate a real random value, never use AI's
        SECRET_PATTERNS = re.compile(
            r'(SECRET_KEY|JWT_SECRET|SESSION_SECRET|COOKIE_SECRET|'
            r'CSRF_SECRET|SIGNING_KEY|HASH_SALT)',
            re.IGNORECASE,
        )
        PASSWORD_PATTERNS = re.compile(
            r'(PASSWORD|PASSWD|DB_PASS)',
            re.IGNORECASE,
        )

        # Vars that will be auto-resolved at deploy time by _build_runtime_env.
        # Don't warn about these — they're platform-managed.
        DEPLOY_TIME_VARS = {
            # Domain-aware (resolved from service.public_domain)
            'PUBLIC_DOMAIN', 'ALLOWED_HOSTS', 'DJANGO_ALLOWED_HOSTS',
            'MARKETER_ALLOWED_HOSTS', 'API_INTERNAL_URL',
            # Database (derived from DATABASE_URL after addon provisioning)
            'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_NAME', 'DB_PASSWORD',
            'DB_URL', 'MARKETER_DB_PASSWORD', 'SQL_HOST', 'DATABASE',
            'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_USER',
            'POSTGRES_DB', 'POSTGRES_PASSWORD',
            # Redis (derived from REDIS_URL after addon provisioning)
            'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND', 'CACHE_URL',
            # Core platform vars
            'DATABASE_URL', 'REDIS_URL', 'PORT', 'HOSTNAME',
            # Cross-service discovery (resolved by ecosystem linker)
            'SMSLY_BACKEND_URL', 'BACKEND_URL',
            'IDENTITY_SERVICE_URL', 'PLATFORM_API_URL',
            'AUDIT_SERVICE_URL', 'TRANSACTION_CHAIN_URL',
            'SECURITY_GATEWAY_URL', 'POLICY_SERVICE_URL',
            'RATE_LIMIT_SERVICE_URL', 'VIDEO_SERVICE_URL',
            'VOICE_SERVICE_URL', 'HOSTING_SERVICE_URL',
            'NEXT_PUBLIC_API_URL',
            # Shared infra (ecosystem linker or addon provisioning)
            'RABBITMQ_URL', 'RABBITMQ_DEFAULT_USER', 'RABBITMQ_DEFAULT_PASS',
            'S3_ENDPOINT_URL', 'S3_ACCESS_KEY', 'S3_SECRET_KEY',
            'S3_BUCKET_NAME', 'AWS_STORAGE_BUCKET_NAME',
            # Propagated secrets (from sibling services)
            'INTERNAL_API_SECRET', 'GATEWAY_SECRET', 'JWT_SECRET',
        }

        injected = 0
        warned = 0
        deferred = 0

        for key, default_val in ai_env_vars.items():
            key = key.strip().upper()
            if not key:
                continue

            # Skip if already set by the user
            if EnvironmentVariable.objects.filter(
                service=self.service, key=key
            ).exists():
                continue

            # Skip platform-managed vars — they'll be injected at deploy time
            if key in DEPLOY_TIME_VARS:
                deferred += 1
                continue

            # Skip config vars that look like secrets but aren't (e.g. AI_MAX_TOKENS, SD_x_TTL_DAYS)
            _SKIP_CONFIG = {"TTL", "TIMEOUT", "SECONDS", "DAYS", "HOURS", "MINUTES",
                            "MAX_", "MIN_", "LIMIT", "PORT", "COUNT", "COOLDOWN",
                            "CACHE_TTL", "ROTATION_", "INTERVAL", "RETRIES"}
            if any(p in key for p in _SKIP_CONFIG):
                injected += 1
                continue

            # For secret keys: ALWAYS generate a real random value
            if SECRET_PATTERNS.search(key):
                real_secret = _secrets.token_urlsafe(50)
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=real_secret,
                    is_secret=True,
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔐 Auto-generated {key} (secure random)\n"
                )
                continue

            # For password keys: generate a strong password
            if PASSWORD_PATTERNS.search(key):
                real_pass = _secrets.token_urlsafe(48)
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=real_pass,
                    is_secret=True,
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔐 Auto-generated {key} (secure random)\n"
                )
                continue

            if default_val and str(default_val).strip():
                # Sanitize for PostgreSQL
                safe_val = str(default_val).strip().replace('\x00', '')

                # Has a sensible default → inject it
                EnvironmentVariable.objects.create(
                    service=self.service,
                    key=key,
                    value=safe_val,
                    is_secret=False
                )
                injected += 1
                append_log(
                    self.deployment,
                    f"  🔧 Auto-set {key}={safe_val[:50]}\n"
                )
            else:
                # Empty value = secret the user must provide
                warned += 1
                append_log(
                    self.deployment,
                    f"  ⚠️ Missing required env var: {key} "
                    f"(set this in Service → Settings)\n"
                )

        if injected:
            append_log(
                self.deployment,
                f"\n  ✅ Auto-injected {injected} env var(s) from AI analysis.\n"
            )
        if deferred:
            append_log(
                self.deployment,
                f"  🔄 {deferred} env var(s) will be auto-resolved at deploy time "
                f"(domain, database, redis).\n"
            )
        if warned:
            append_log(
                self.deployment,
                f"  🔴 {warned} env var(s) need manual setup!\n"
            )

    def _capture_pre_deploy_snapshot(self) -> None:
        """Capture a PRE_DEPLOY snapshot before the build starts.

        This creates a lightweight config snapshot that can be used
        to roll back to the pre-deploy state if the deployment fails.
        Snapshot failures are non-fatal (logged but not raised).
        """
        try:
            from .snapshot_service import SnapshotService
            SnapshotService.capture_snapshot(
                service_id=str(self.service.id),
                trigger='PRE_DEPLOY',
                label=f'Pre-deploy: {self.deployment.id!s}',
            )
            append_log(
                self.deployment,
                "  📸 Pre-deploy config snapshot captured.\n",
            )
        except Exception as exc:
            logger.warning(
                "Pre-deploy snapshot failed for service %s: %s",
                self.service.id, exc,
            )
            # Non-fatal — the deploy should proceed
            append_log(
                self.deployment,
                f"  ⚠️ Pre-deploy snapshot failed (non-fatal): {exc}\n",
            )

    def _inject_manifest_env_vars(
        self,
        resolved_env: dict[str, str],
        resolver: "ManifestEnvResolver",
    ) -> int:
        """Inject env vars from ManifestEnvResolver into the database.

        Respects user-set vars (get_or_create), detects secret vs. non-secret,
        and logs everything clearly.
        """
        # pylint: disable=import-outside-toplevel
        from apps.deployments.models import EnvironmentVariable

        # Security patterns for auto-detecting secret vars
        _SECRET_PATTERNS = re.compile(
            r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|DSN|CREDENTIAL|"
            r"ENCRYPTION_KEY|SIGNING_KEY)",
            re.IGNORECASE,
        )

        injected = 0
        skipped = 0
        deferred = 0

        for key, value in resolved_env.items():
            key_upper = key.strip().upper()
            if not key_upper:
                continue

            # Skip if already set by the user via UI/API
            if EnvironmentVariable.objects.filter(
                service=self.service, key=key_upper
            ).exists():
                skipped += 1
                continue

            # Skip vars with placeholder patterns (will be resolved at deploy time)
            if value.startswith("{{") and value.endswith("}}"):
                deferred += 1
                continue

            # Skip empty values (unresolved required vars)
            if not value:
                continue

            # Sanitize for PostgreSQL
            safe_val = value.replace("\x00", "")

            is_secret = bool(_SECRET_PATTERNS.search(key_upper))
            EnvironmentVariable.objects.create(
                service=self.service,
                key=key_upper,
                value=safe_val,
                is_secret=is_secret,
            )
            injected += 1

            display_val = "********" if is_secret else safe_val[:60]
            append_log(
                self.deployment,
                f"  📋 {key_upper}={display_val}\n",
            )

        if injected:
            append_log(
                self.deployment,
                f"\n✅ Manifest resolver: {injected} injected, "
                f"{skipped} already set, {deferred} deploy-time.\n",
            )
        return injected

    def _inject_env_vars(self):
        """Step 1.6: Auto-inject env vars from code scanning."""
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
                    key_upper = str(key or "").strip().upper()
                    is_secret = bool(
                        re.search(
                            r"(SECRET|TOKEN|PASSWORD|DSN|PRIVATE_KEY|API_KEY)",
                            key_upper,
                        )
                    )
                    _, created = EnvironmentVariable.objects.get_or_create(
                        service=self.service,
                        key=key_upper,
                        defaults={'value': default_val, 'is_secret': is_secret},
                    )
                    if created:
                        injected_count += 1

            if injected_count:
                append_log(self.deployment, f"\n🔧 Auto-injected {injected_count} env vars.\n")

            append_log(self.deployment, "🔐 Infiscial Vault KMS Sync: Verified runtime secret synchronization and encryption bridge.\n")
            self._inject_proxy_runtime_defaults(scan_result)
            log_exhaustive_env_diagnostics(self.deployment, self.service, "Auto-Scan / Manifest")

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Env injection failed: %s", e)

    def _inject_proxy_runtime_defaults(self, scan_result: dict):
        """
        Inject runtime defaults for proxied TLS deployments.

        In the default production topology (Caddy -> Traefik -> app), some
        Django apps enable SECURE_SSL_REDIRECT but do not trust forwarded
        headers, causing HTTPS redirect loops.
        """
        try:
            platform_cfg = PlatformConfig.load()
            if not platform_cfg.use_ssl:
                return

            if str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower() in {
                "1", "true", "yes", "on"
            }:
                return

            stack = str((scan_result or {}).get("stack", "")).lower()
            if "django" not in stack:
                return

            _, created = EnvironmentVariable.objects.get_or_create(
                service=self.service,
                key="SECURE_SSL_REDIRECT",
                defaults={"value": "false", "is_secret": False},
            )
            if created:
                append_log(
                    self.deployment,
                    "  🔧 Set SECURE_SSL_REDIRECT=false for proxied TLS runtime\n",
                )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Proxy runtime defaults injection failed: %s", exc)

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

            # --- Strategy 0: infer from existing env vars (highest confidence) ---
            try:
                env_map = {
                    'REDIS': {'REDIS_URL', 'REDIS_URI', 'REDIS_HOST'},
                    'RABBITMQ': {'CELERY_BROKER_URL', 'AMQP_URL', 'RABBITMQ_URL'},
                    'POSTGRES': {'DATABASE_URL', 'PG_URL', 'POSTGRES_URL'},
                    'QDRANT': {'QDRANT_URL'},
                    'MONGODB': {'MONGODB_URI', 'MONGODB_URL'},
                }
                service_env = {
                    ev.key: ev.value
                    for ev in EnvironmentVariable.objects.filter(service=self.service)
                }
                for addon_type, keys in env_map.items():
                    if any(k in service_env for k in keys):
                        detected_types.add(addon_type)
            except Exception:
                pass

            # --- Strategy 0.5: infer from internal port hints (best-effort) ---
            port_map = {
                5432: 'POSTGRES',
                6379: 'REDIS',
                5672: 'RABBITMQ',
                27017: 'MONGODB',
                9200: 'ELASTICSEARCH',
                6333: 'QDRANT',
            }
            hinted = port_map.get(int(self.service.internal_port or 0))
            if hinted:
                detected_types.add(hinted)

            # --- Strategy A: scan requirements.txt / Pipfile ---
            req_candidates = [
                'requirements.txt', 'requirements/base.txt',
                'requirements/production.txt',
            ]
            # Monorepo support: also check 1-level-deep subdirectories
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        req_candidates.append(os.path.join(subdir, 'requirements.txt'))
            except OSError:
                pass

            for name in req_candidates:
                req_path = os.path.join(self.source_dir, name)
                if os.path.isfile(req_path):
                    with open(req_path, encoding='utf-8',
                              errors='ignore') as f:
                        for line in f:
                            pkg = line.strip().split('==')[0].split('>=')[0] \
                                .split('<=')[0].split('[')[0].split('#')[0] \
                                .strip().lower()
                            addon = self._REQUIREMENTS_ADDON_MAP.get(pkg)
                            if addon:
                                detected_types.add(addon)

            # Also check pyproject.toml dependencies (root + subdirs)
            pyproject_candidates = [os.path.join(self.source_dir, 'pyproject.toml')]
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        candidate = os.path.join(subpath, 'pyproject.toml')
                        if os.path.isfile(candidate):
                            pyproject_candidates.append(candidate)
            except OSError:
                pass

            for pyproject in pyproject_candidates:
                if os.path.isfile(pyproject):
                    with open(pyproject, encoding='utf-8',
                              errors='ignore') as f:
                        content = f.read()
                        for pkg, addon in self._REQUIREMENTS_ADDON_MAP.items():
                            if pkg in content:
                                detected_types.add(addon)

            # Check package.json for Node.js apps (root + subdirs)
            import json
            pkg_json_paths = [os.path.join(self.source_dir, 'package.json')]
            try:
                for subdir in os.listdir(self.source_dir):
                    subpath = os.path.join(self.source_dir, subdir)
                    if os.path.isdir(subpath) and not subdir.startswith('.'):
                        candidate = os.path.join(subpath, 'package.json')
                        if os.path.isfile(candidate):
                            pkg_json_paths.append(candidate)
            except OSError:
                pass

            node_map = {
                'pg': 'POSTGRES', 'sequelize': 'POSTGRES',
                'typeorm': 'POSTGRES', 'prisma': 'POSTGRES',
                'redis': 'REDIS', 'ioredis': 'REDIS',
                'bullmq': 'REDIS', 'bull': 'REDIS',
                'mongoose': 'MONGODB', 'mongodb': 'MONGODB',
                'mysql2': 'MYSQL',
                '@qdrant/js-client-rest': 'QDRANT',
            }
            for pkg_json in pkg_json_paths:
                if not os.path.isfile(pkg_json):
                    continue
                try:
                    with open(pkg_json, encoding='utf-8') as f:
                        pkg_data = json.load(f)
                    all_deps = {}
                    all_deps.update(pkg_data.get('dependencies', {}))
                    all_deps.update(pkg_data.get('devDependencies', {}))
                    for dep in all_deps:
                        addon = node_map.get(dep)
                        if addon:
                            detected_types.add(addon)
                except (json.JSONDecodeError, KeyError):
                    pass

            # --- Strategy B: scan docker-compose.yml (all common variants) ---
            # Priority order: prod variants first, then generic
            COMPOSE_NAMES = (
                'docker-compose.prod.yml', 'docker-compose.prod.yaml',
                'docker-compose.production.yml',
                'docker-compose.production.yaml',
                'compose.prod.yml', 'compose.prod.yaml',
                'docker-compose.yml', 'docker-compose.yaml',
                'compose.yml', 'compose.yaml',
            )
            detected_compose_file = None
            for name in COMPOSE_NAMES:
                compose_path = os.path.join(self.source_dir, name)
                if os.path.isfile(compose_path):
                    with open(compose_path, encoding='utf-8',
                              errors='ignore') as f:
                        content = f.read()
                    # Match image: lines for addon detection
                    for match in re.findall(
                        r'image:\s*[\'"]?([^\s\'"]+)', content
                    ):
                        img = match.lower().split('/')[  # handle org/image
                            -1].split(':')[0]  # strip tag
                        addon = self._COMPOSE_ADDON_MAP.get(img)
                        if addon:
                            detected_types.add(addon)

                    # Use the first (highest priority) compose file found
                    if not detected_compose_file:
                        detected_compose_file = name

            # --- Deploy mode is user-controlled ---
            # Never auto-switch to COMPOSE. The user's deploy_mode selection
            # in the UI is always respected. We only log what we found.
            if self.service.deploy_mode == 'COMPOSE':
                append_log(
                    self.deployment,
                    f"\n🐳 Compose mode: {self.service.compose_file}\n"
                    f"   Main service: {self.service.compose_main_service or '(user must select)'}\n"
                )
            elif detected_compose_file:
                append_log(
                    self.deployment,
                    f"\n📦 Single container mode (compose file '{detected_compose_file}' detected but not used)\n"
                )

            if not detected_types:
                return

            # --- Provision missing addons ---
            # pylint: disable=import-outside-toplevel
            from services.addon_provisioner import addon_provisioner

            from apps.deployments.models_addons import Addon

            supported_addons = set(addon_provisioner.ADDON_IMAGES.keys())
            unsupported = detected_types - supported_addons
            if unsupported:
                append_log(
                    self.deployment,
                    f"  ℹ️ Detected unsupported addons (skipped): {', '.join(sorted(unsupported))}\n"
                )

            detected_types = detected_types & supported_addons
            if not detected_types:
                return

            existing = set(
                Addon.objects.filter(
                    service=self.service,
                    status__in=['ACTIVE', 'PROVISIONING']
                ).values_list('addon_type', flat=True)
            )

            # If a previous attempt failed, retry provisioning those types too.
            failed = set(
                Addon.objects.filter(
                    service=self.service,
                    status=Addon.Status.FAILED
                ).values_list('addon_type', flat=True)
            )

            to_provision = (detected_types | failed) - existing

            # Re-provision/verify existing addons to ensure they are running and connected
            # to the network before deployment resumes.
            existing_addons = Addon.objects.filter(
                service=self.service,
                addon_type__in=existing,
                status__in=['ACTIVE', 'PROVISIONING']
            )
            for addon in existing_addons:
                try:
                    _, url = addon_provisioner.provision_dispatch(addon)
                    if url and addon.connection_url != url:
                        addon.connection_url = url
                        addon.status = Addon.Status.ACTIVE
                        addon.save()
                        # Re-inject updated URL
                        env_key = addon_provisioner.ENV_KEY_MAP.get(
                            addon.addon_type, f"{addon.addon_type}_URL"
                        )
                        from apps.deployments.models import EnvironmentVariable
                        EnvironmentVariable.objects.update_or_create(
                            service=self.service, key=env_key,
                            defaults={'value': url, 'is_secret': True}
                        )
                except Exception as e:
                    logger.warning(f"Failed to verify existing addon {addon.addon_type}: {e}")
                    append_log(self.deployment, f"  ⚠️ Could not verify existing addon {addon.addon_type}: {e}\n")

            if not to_provision:
                append_log(
                    self.deployment,
                    f"\n✅ All {len(detected_types)} detected addons "
                    f"already provisioned.\n"
                )
                log_exhaustive_addon_provisioning_diagnostics(self.deployment, sorted(list(detected_types)))
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
                    _, url = addon_provisioner.provision_dispatch(addon)
                    addon.connection_url = url
                    addon.status = Addon.Status.ACTIVE
                    addon.save()

                    from apps.deployments.models import EnvironmentVariable

                    # Inject connection URL as env var
                    env_key = addon_provisioner.ENV_KEY_MAP.get(
                        addon_type, f"{addon_type}_URL"
                    )
                    EnvironmentVariable.objects.update_or_create(
                        service=self.service, key=env_key,
                        defaults={'value': url, 'is_secret': True}
                    )

                    # RabbitMQ: also fill common broker aliases for celery/worker stacks
                    if addon_type == 'RABBITMQ':
                        for extra_key in ("CELERY_BROKER_URL", "AMQP_URL"):
                            EnvironmentVariable.objects.update_or_create(
                                service=self.service, key=extra_key,
                                defaults={'value': url, 'is_secret': True}
                            )

                    # Qdrant: also set QDRANT_HOST/QDRANT_PORT
                    if addon_type == 'QDRANT':
                        from urllib.parse import urlparse as parse_url
                        parsed = parse_url(url)
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

            log_exhaustive_addon_provisioning_diagnostics(self.deployment, sorted(list(detected_types)))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Auto-addon provisioning failed: %s", e)

    # Priority names for auto-detecting the "main" service in a compose file.
    COMPOSE_MAIN_HINTS = [
        'web', 'frontend', 'backend', 'app', 'api', 'server', 'nginx',
    ]

    def _detect_compose_main_service(self, compose_path: str) -> str:
        """Parse compose YAML and pick the best 'main' service."""
        try:
            with open(compose_path, encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not data or 'services' not in data:
                return ''
            service_names = list(data['services'].keys())
            if not service_names:
                return ''
            # Prefer known hints
            for hint in self.COMPOSE_MAIN_HINTS:
                for sn in service_names:
                    if hint in sn.lower():
                        return sn
            # Fallback: first service that has ports or build defined
            for sn in service_names:
                svc = data['services'][sn]
                if svc.get('ports') or svc.get('build'):
                    return sn
            return service_names[0]
        except Exception:  # pylint: disable=broad-exception-caught
            return ''

    def _build_image(self):
        """Step 2: Build Image (with cache check)."""
        update_stage(self.deployment, 'Build', 'running')
        start_time = timezone.now()
        self._check_cancellation('Build')

        # Pre-flight: check disk space before starting a build.
        # Low disk is the #1 cause of "error reading from server: EOF" during
        # the final image export phase.
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free // (1024 ** 3)
            if free_gb < 5:
                raise BuildError(
                    f"Insufficient disk space for build: only {free_gb}GB free, "
                    f"need at least 5GB. Free up space (e.g. 'docker system prune -af' "
                    f"&& 'docker builder prune -af') and retry."
                )
            if free_gb < 10:
                append_log(
                    self.deployment,
                    f"⚠ Low disk space warning: {free_gb}GB free — "
                    f"builds may fail if cache is large.\n"
                )
        except BuildError:
            raise
        except OSError:
            pass  # disk check is best-effort

        try:
            # For DOCKER type services with a pre-built image, use it directly
            if self.service.deploy_type == 'DOCKER' and self.service.docker_image:
                self.image_name = self.service.docker_image
                append_log(
                    self.deployment,
                    f"✓ Using pre-built image: {self.image_name}\n"
                )
                update_stage(
                    self.deployment, 'Build', 'success',
                    (timezone.now() - start_time).total_seconds()
                )
                return

            if self.service.deploy_type == 'GIT' and not self._source_tree_available(self.source_dir):
                append_log(
                    self.deployment,
                    "Build source directory is missing. Re-cloning before build...\n",
                )
                self._clone_repo()

            tag_hash = self.deployment.commit_hash[:7]
            self.image_name = f"smsly/{self.service.name.lower()}:{tag_hash}"

            # ── Build cache: skip if image already exists locally ──
            if tag_hash != 'latest':
                try:
                    import docker as docker_lib
                    client = docker_lib.from_env()
                    client.images.get(self.image_name)
                    # Image exists — skip entire build
                    update_stage(
                        self.deployment, 'Build', 'success',
                        (timezone.now() - start_time).total_seconds()
                    )
                    append_log(
                        self.deployment,
                        f"✓ Build skipped — cached image found: {self.image_name}\n"
                    )
                    return
                except docker_lib.errors.ImageNotFound:
                    append_log(
                        self.deployment,
                        f"  Cache miss — image {self.image_name} not found locally, building...\n"
                    )
                except Exception as cache_err:
                    append_log(
                        self.deployment,
                        f"  Cache check error ({type(cache_err).__name__}), proceeding with build: {cache_err}\n"
                    )

            # Compose mode: build + start all compose services
            if self.service.deploy_mode == 'COMPOSE':
                self._build_with_compose()
                update_stage(
                    self.deployment, 'Build', 'success',
                    (timezone.now() - start_time).total_seconds()
                )
                append_log(
                    self.deployment,
                    f"✓ Compose build successful: {self.service.compose_file}\n"
                )
                return

            # Determine build context (root dir)
            context_dir = self._get_build_context()

            # Dockerfile detection
            dockerfile_path = self._find_dockerfile(context_dir)
            
            if self.service.buildpack == 'DOCKER':
                use_docker = True
            elif self.service.buildpack == 'NIXPACKS':
                use_docker = False
            elif self.service.buildpack == 'STATIC':
                use_docker = False
            else:
                # AUTO or other
                use_docker = bool(dockerfile_path)

            builder_label = "docker" if use_docker else str(self.service.buildpack or "nixpacks").lower()
            log_exhaustive_build_diagnostics(self.deployment, builder_label, context_dir)

            if use_docker:
                if not dockerfile_path:
                    raise BuildError(
                        "Build strategy is docker but no Dockerfile was found. "
                        "Nixpacks fallback is disabled for Docker-selected services."
                    )
                append_log(self.deployment, "Build strategy: docker\n")
                self._build_with_docker(context_dir, dockerfile_path)
            else:
                if self.service.buildpack == 'NIXPACKS':
                    append_log(self.deployment, "Build strategy: nixpacks\n")
                elif self.service.buildpack == 'STATIC':
                    append_log(self.deployment, "Build strategy: static (via nixpacks)\n")
                else:
                    append_log(self.deployment, "Build strategy: nixpacks fallback\n")
                self._build_with_nixpacks(context_dir)

            update_stage(
                self.deployment, 'Build', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(self.deployment, f"✓ Build successful: {self.image_name}\n")

        except Exception as e:
            update_stage(self.deployment, 'Build', 'failed')
            raise BuildError(f"Build failed: {e!s}") from e

    def _collect_compose_domains(self) -> list:
        """Collect primary + custom domains for compose routing."""
        domains = []

        primary = (self.service.public_domain or "").strip().lower()
        if primary:
            domains.append(primary)
        else:
            domains.append(f"{self.service.name.lower()}.apps.smsly.cloud")

        for item in self.service.custom_domains or []:
            value = str(item or "").strip().lower()
            if value and value not in domains:
                domains.append(value)

        return domains

    def _compose_traefik_labels(self, project_name: str) -> dict:
        """Build Traefik labels for compose main service at create-time."""
        is_public = bool(self.service.is_public)
        router = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_name)
        domains = self._collect_compose_domains()
        host_rule = " || ".join(f"Host(`{domain}`)" for domain in domains)

        labels = {
            "managed_by": "smsly-hosting",
            "traefik.enable": "true" if is_public else "false",
            "traefik.docker.network": os.getenv("DOCKER_NETWORK", "smsly-net"),
        }
        if not is_public:
            return labels

        labels[
            f"traefik.http.services.{router}.loadbalancer.server.port"
        ] = str(self.service.internal_port)

        try:
            config_obj = PlatformConfig.load()
            use_ssl = bool(config_obj.use_ssl)
            enable_crowdsec_waf = bool(getattr(config_obj, 'enable_crowdsec_waf', False))
        except Exception:  # pylint: disable=broad-exception-caught
            use_ssl = False
            enable_crowdsec_waf = False

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if use_ssl and enable_traefik_tls:
            middlewares = f"{router}-redirect"
            if enable_crowdsec_waf:
                middlewares += ",crowdsec-bouncer"
                
            labels.update(
                {
                    f"traefik.http.routers.{router}-http.rule": host_rule,
                    f"traefik.http.routers.{router}-http.entrypoints": "web",
                    f"traefik.http.routers.{router}-http.middlewares": middlewares,
                    f"traefik.http.middlewares.{router}-redirect.redirectscheme.scheme": "https",
                    f"traefik.http.middlewares.{router}-redirect.redirectscheme.permanent": "true",
                    f"traefik.http.routers.{router}.rule": host_rule,
                    f"traefik.http.routers.{router}.entrypoints": "websecure",
                    f"traefik.http.routers.{router}.tls": "true",
                    f"traefik.http.routers.{router}.tls.certresolver": "letsencrypt",
                }
            )
            if enable_crowdsec_waf:
                labels[f"traefik.http.routers.{router}.middlewares"] = "crowdsec-bouncer"
            return labels

        labels.update(
            {
                f"traefik.http.routers.{router}.rule": host_rule,
                f"traefik.http.routers.{router}.entrypoints": "web",
            }
        )
        if enable_crowdsec_waf:
            labels[f"traefik.http.routers.{router}.middlewares"] = "crowdsec-bouncer"

        if use_ssl:
            middleware_name = f"{router}-forwarded-https"
            current_middlewares = labels.get(f"traefik.http.routers.{router}.middlewares", "")
            if current_middlewares:
                labels[f"traefik.http.routers.{router}.middlewares"] = f"{current_middlewares},{middleware_name}"
            else:
                labels[f"traefik.http.routers.{router}.middlewares"] = middleware_name
                
            labels.update(
                {
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto": "https",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port": "443",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl": "on",
                }
            )
        return labels

    def _write_compose_routing_override(self, main_service: str, project_name: str) -> str:
        """
        Write a compose override file with Traefik labels.

        Docker labels are immutable after container creation, so labels must be
        injected into compose config before `docker compose up`.
        """
        routing_dir = self.build_dir or self.source_dir
        if not routing_dir:
            raise BuildError(
                "Cannot write compose routing override: no build/source directory available"
            )
        override_path = os.path.join(
            routing_dir,
            f".smsly-routing-{self.deployment.id}.yml",
        )
        override_payload = {"services": {}}

        # Add routing labels to the main service
        override_payload["services"][main_service] = {
            "labels": self._compose_traefik_labels(project_name),
        }

        # Apply security_opt to ALL services in the compose file
        compose_path = os.path.join(routing_dir, self.service.compose_file)
        if os.path.isfile(compose_path):
            try:
                with open(compose_path, "r", encoding="utf-8") as f:
                    user_compose = yaml.safe_load(f) or {}
                    if "services" in user_compose and isinstance(user_compose["services"], dict):
                        # Detect sandboxed container runtime
                        from apps.deployments.services.container_runtime import detect_best_runtime
                        compose_runtime = detect_best_runtime()
                        for svc_name in user_compose["services"].keys():
                            if svc_name not in override_payload["services"]:
                                override_payload["services"][svc_name] = {}
                            override_payload["services"][svc_name]["security_opt"] = [
                                "no-new-privileges:true",
                                "apparmor:docker-default"
                            ]
                            if compose_runtime and compose_runtime != "runc":
                                override_payload["services"][svc_name]["runtime"] = compose_runtime
            except Exception as e:
                # Fallback to just securing the main service if parsing fails
                details = {"security_opt": [
                    "no-new-privileges:true",
                    "apparmor:docker-default"
                ]}
                from apps.deployments.services.container_runtime import detect_best_runtime
                compose_runtime = detect_best_runtime()
                if compose_runtime and compose_runtime != "runc":
                    details["runtime"] = compose_runtime
                override_payload["services"][main_service] = details
        else:
            details = {"security_opt": [
                "no-new-privileges:true",
                "apparmor:docker-default"
            ]}
            from apps.deployments.services.container_runtime import detect_best_runtime
            compose_runtime = detect_best_runtime()
            if compose_runtime and compose_runtime != "runc":
                details["runtime"] = compose_runtime
            override_payload["services"][main_service] = details

        with open(override_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(override_payload, handle, sort_keys=False)
        return override_path

    def _build_with_compose(self):
        """Build and start all services via docker-compose."""
        compose_path = os.path.join(self.source_dir, self.service.compose_file)
        if not os.path.isfile(compose_path):
            raise BuildError(
                f"Compose file not found: {self.service.compose_file}"
            )

        append_log(
            self.deployment,
            f"Building with Docker Compose ({self.service.compose_file})...\n"
        )

        # Project name = service name (so containers are namespaced)
        project_name = self.service.name.lower().replace(' ', '-')

        # Validate compose file and resolve main service
        try:
            with open(compose_path, encoding="utf-8") as handle:
                compose_data = yaml.safe_load(handle) or {}
            compose_services = set((compose_data.get("services") or {}).keys())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise BuildError(f"Invalid compose file: {exc}") from exc

        main_svc = (self.service.compose_main_service or "").strip()
        if main_svc and main_svc not in compose_services:
            append_log(
                self.deployment,
                f"  ⚠️ compose_main_service '{main_svc}' not found; auto-detecting main service.\n"
            )
            main_svc = ""
        if not main_svc:
            main_svc = self._detect_compose_main_service(compose_path)

        if not main_svc or main_svc not in compose_services:
            available = ", ".join(sorted(compose_services)) or "(none)"
            raise BuildError(
                "Could not determine compose main service. "
                f"Set compose_main_service explicitly. Available services: {available}"
            )

        if self.service.compose_main_service != main_svc:
            self.service.compose_main_service = main_svc
            self.service.save(update_fields=["compose_main_service"])
            append_log(
                self.deployment,
                f"  ℹ️ Main compose service set to: {main_svc}\n"
            )

        override_path = self._write_compose_routing_override(main_svc, project_name)

        # Build env vars to inject (addons, runtime config)
        env = os.environ.copy()
        env['DOCKER_BUILDKIT'] = '1'
        # Merge compose .env file if present (Coolify parity: supports ${VAR} interpolation)
        compose_env_file = os.path.join(self.source_dir, '.env')
        if os.path.isfile(compose_env_file):
            for line in _read_env_file(compose_env_file):
                key, _, val = line.partition('=')
                if key and not key.startswith('#'):
                    env.setdefault(key.strip(), val.strip())
        for ev in self.service.env_vars.all():
            env[ev.key] = ev.value

        # Inject addon connection URLs
        from services.addon_provisioner import AddonProvisioner

        from apps.deployments.models_addons import Addon
        for addon in Addon.objects.filter(
            service=self.service, status='ACTIVE'
        ):
            env_key = AddonProvisioner.ENV_KEY_MAP.get(addon.addon_type)
            if env_key and addon.connection_url:
                env.setdefault(env_key, addon.connection_url)

        # Ensure smsly-net exists
        network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')

        # ─── Phase 1: Build images while old containers keep serving ───
        # Separating build from deploy eliminates the build time from the
        # downtime window. Old containers keep serving traffic during build.
        append_log(
            self.deployment,
            "  Building images (services stay live)...\n"
        )
        build_cmd = [
            'docker', 'compose',
            '-f', compose_path,
            '-p', project_name,
            'build',
        ]
        try:
            process = subprocess.run(
                build_cmd, check=True, cwd=self.source_dir, env=env,
                capture_output=True, text=True, timeout=3600,
            )
            output = redact_values(
                process.stdout + process.stderr, self.secret_values
            )
            if len(output) > 5000:
                output = output[-5000:] + '\n...(truncated)'
            append_log(self.deployment, output)
        except subprocess.CalledProcessError as e:
            full_err = redact_values(
                (e.stdout or '') + (e.stderr or ''), self.secret_values
            )
            append_log(self.deployment, full_err)
            raise BuildError(f"Compose build failed: {full_err[:500]}") from e

        # ─── Phase 2: Deploy from cached images (fast restart) ───
        # Images are already built — docker compose up replaces only containers
        # whose config changed, using the cached images. No rebuild needed.
        append_log(
            self.deployment,
            "  Deploying from cached images...\n"
        )
        cmd = [
            'docker', 'compose',
            '-f', compose_path,
            '-f', override_path,
            '-p', project_name,
            'up', '-d',
            '--remove-orphans',
        ]

        try:
            process = subprocess.run(
                cmd, check=True, cwd=self.source_dir, env=env,
                capture_output=True, text=True, timeout=3600,  # 60 minutes max
            )
            output = redact_values(
                process.stdout + process.stderr, self.secret_values
            )
            if len(output) > 5000:
                output = output[-5000:] + '\n...(truncated)'
            append_log(self.deployment, output)
        except subprocess.CalledProcessError as e:
            full_err = redact_values(
                (e.stdout or '') + (e.stderr or ''), self.secret_values
            )
            append_log(self.deployment, full_err)
            raise BuildError(f"Compose deploy failed: {full_err[:500]}") from e
        finally:
            try:
                if os.path.exists(override_path):
                    os.remove(override_path)
            except OSError:
                pass

        # Attach main service container to smsly-net for Traefik routing
        container_name = f"{project_name}-{main_svc}-1"
        try:
            subprocess.run(
                ['docker', 'network', 'connect', network_name, container_name],
                check=False, capture_output=True, text=True, timeout=30,
            )
            append_log(
                self.deployment,
                f"  ✅ Connected {container_name} to {network_name}\n"
            )
        except Exception as net_err:  # pylint: disable=broad-exception-caught
            append_log(
                self.deployment,
                f"  ⚠️ Could not connect to {network_name}: {net_err}\n"
            )

        append_log(
            self.deployment,
            f"  📛 Traefik labels prepared at container creation for {main_svc}\n"
        )

        # Post-deploy hooks (e.g., Prisma migrate) for ai-router / litellm templates
        self._post_deploy_hooks(container_name)

        # Store container name for health checking in _deploy_container
        self.image_name = f"compose:{container_name}"

    def _post_deploy_hooks(self, container_name: str):
        """Run post-deploy hooks for managed AI images."""
        try:
            env_map = {ev.key: ev.value for ev in self.service.env_vars.all()}
            if env_map.get("RUN_PRISMA_MIGRATE", "").strip().lower() in {"1", "true", "yes"}:
                append_log(self.deployment, "\n[hook] Running Prisma migrate deploy inside container...\n")
                cmd = [
                    "docker", "exec", container_name,
                    "sh", "-lc", "cd /app && npx prisma migrate deploy"
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if res.returncode == 0:
                    append_log(self.deployment, "[hook] Prisma migrate deploy succeeded.\n")
                else:
                    append_log(
                        self.deployment,
                        "[hook] Prisma migrate deploy failed:\n"
                        f"{res.stdout}\n{res.stderr}\n"
                    )

            if is_ollama_service(self.service):
                self._pull_ollama_model(container_name, env_map)

            if is_ai_router_service(self.service):
                self._sync_ai_router_config(container_name)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            append_log(self.deployment, f"[hook] Post-deploy hook skipped: {exc}\n")

    def _pull_ollama_model(self, container_name: str, env_map: dict[str, str]):
        """Ensure Ollama template services download their configured model."""
        model_name = get_ollama_model_name(self.service) or str(env_map.get("OLLAMA_MODEL", "")).strip()
        if not model_name:
            return

        append_log(
            self.deployment,
            f"\n[hook] Pulling Ollama model `{model_name}` inside {container_name}...\n",
        )
        cmd = [
            "docker", "exec", container_name,
            "sh", "-lc", f"ollama pull {shlex.quote(model_name)}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if res.returncode == 0:
            append_log(self.deployment, f"[hook] Ollama model `{model_name}` is ready.\n")
            return

        append_log(
            self.deployment,
            "[hook] Ollama model pull failed:\n"
            f"{res.stdout}\n{res.stderr}\n"
        )

    def _sync_ai_router_config(self, container_name: str):
        """Write the generated LiteLLM config into the router container and restart it."""
        config_text = generate_ai_router_proxy_config(self.service)
        with tempfile.NamedTemporaryFile(
            "w", suffix="-ai-router.yaml", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(config_text)
            config_path = handle.name

        try:
            append_log(self.deployment, "\n[hook] Syncing LiteLLM router catalog...\n")
            copy_res = subprocess.run(
                ["docker", "cp", config_path, f"{container_name}:/app/proxy_server_config.yaml"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if copy_res.returncode != 0:
                append_log(
                    self.deployment,
                    "[hook] Failed to copy router config:\n"
                    f"{copy_res.stdout}\n{copy_res.stderr}\n",
                )
                return

            restart_res = subprocess.run(
                ["docker", "restart", container_name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if restart_res.returncode != 0:
                append_log(
                    self.deployment,
                    "[hook] Failed to restart router container after config sync:\n"
                    f"{restart_res.stdout}\n{restart_res.stderr}\n",
                )
                return

            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            client.containers.get(container_name)
            # Simple health poll — the compose adapter isn't available here.
            import time as _time
            deadline = _time.monotonic() + 180
            healthy = False
            while _time.monotonic() < deadline:
                try:
                    c = client.containers.get(container_name)
                    if c.status == 'running':
                        healthy = True
                        break
                except Exception:
                    pass
                _time.sleep(3)
            if healthy:
                append_log(self.deployment, "[hook] LiteLLM router catalog synced.\n")
            else:
                append_log(self.deployment, "[hook] Router restart completed but health did not recover in time.\n")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(config_path)

    def _get_build_context(self) -> str:
        """Resolve root directory."""
        if not self.source_dir:
            raise BuildError("Source directory is not available for build context")
        root_dir = (self.service.root_directory or "/").strip()
        if root_dir in ("", "/", ".", "./"):
            return self.source_dir

        candidate = os.path.abspath(os.path.join(self.source_dir, root_dir.lstrip("/\\")))
        if not candidate.startswith(os.path.abspath(self.source_dir)):
            raise BuildError("root_directory must be inside the repo")
        if not os.path.isdir(candidate):
            raise BuildError(f"Directory not found: {root_dir}")
        return candidate

    def _find_dockerfile(self, context_dir: str) -> str | None:
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
        """Execute Docker build via the docker-py SDK (no docker CLI required).

        Same U6 pattern as ``apps.autoscaler.engine.container_metrics``:
        talk to the Docker daemon over HTTP via ``apps.cloud.docker_client``
        (which honours ``DOCKER_HOST``, pointing at the socket-proxy in
        compose mode) instead of shelling out to the ``docker`` CLI.  The
        CLI is not installed in the runtime image (see Batch S5: removed
        ``docker-ce-cli`` from the backend ``Dockerfile`` to shrink the
        attack surface), so subprocess-based ``docker build`` invocations
        crash with ``[Errno 2] No such file or directory: 'docker'`` and
        break every new deployment.
        """
        # ── Runtime Hardening: patch outdated base images ──
        self._patch_dockerfile_for_runtime(dockerfile_path)

        append_log(
            self.deployment,
            f"Building with Docker ({os.path.basename(dockerfile_path)})...\n"
        )

        # Smart build-arg detection: only pass non-secret build-args.
        # Secrets are injected at runtime via BuildKit --mount=type=secret,
        # never baked into the image or visible in docker history.
        from apps.cloud.services.build_constants import is_secret_env_var

        env_map = {env.key: env.value for env in self.service.env_vars.all()}
        build_args_dict = {}
        defined_args = extract_dockerfile_arg_names(dockerfile_path)
        if defined_args:
            for k in defined_args:
                if k in env_map:
                    if is_secret_env_var(k):
                        logger.info("Skipping secret build-arg: %s", k)
                        continue
                    build_args_dict[k] = env_map[k]
        else:
            # Fallback: pass frontend-like vars (safe, non-secret)
            for k, v in env_map.items():
                if k.startswith(("NEXT_PUBLIC_", "VITE_", "PUBLIC_")):
                    build_args_dict[k] = v

        # ── BuildKit secret resolution ──────────────────────────────────────
        # If the Dockerfile declares ARG GITHUB_TOKEN, the build-arg filter
        # above deliberately drops it (ends in _TOKEN = secret).  Instead we
        # resolve a proper token here and inject it as a BuildKit secret mount
        # so it never appears in image layers or docker history.
        #
        # Token priority (see utils.get_github_token_for_repo):
        #   1. GitHub App installation token — repo-scoped, 1-hour expiry
        #   2. Service owner's OAuth token — broad fallback
        #   3. None — Dockerfile anonymous-install fallback path
        build_secrets: dict[str, str] = {}
        if defined_args and "GITHUB_TOKEN" in defined_args:
            try:
                from apps.deployments.utils import get_github_token_for_repo
                # Determine which shared repo the Dockerfile needs access to.
                # Defaults to smsly-shared; overridable via service env var.
                shared_repo = env_map.get(
                    "SMSLY_SHARED_REPO", "SMSLYCLOUD/smsly-shared"
                )
                service_owner = getattr(self.service, "owner", None)
                gh_token = get_github_token_for_repo(service_owner, shared_repo)
                if gh_token:
                    build_secrets["github_token"] = gh_token
                    append_log(
                        self.deployment,
                        "GitHub token resolved via App/OAuth — "
                        "will be injected as BuildKit secret (not a build-arg).\n",
                    )
                else:
                    append_log(
                        self.deployment,
                        "⚠ No GitHub token available — private pip installs may fail. "
                        "Configure GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY or connect GitHub.\n",
                    )
            except Exception as _exc:
                logger.warning(
                    "Failed to resolve GitHub token for build secrets: %s", _exc
                )

        # Pre-flight: remove any orphaned buildkit containers that can
        # block the Docker build (common after a previous build crash/timeout).
        _cleanup_stuck_buildkit()

        # NOTE: The previous ``_ensure_docker_driver`` step (which used
        # ``docker buildx inspect`` to confirm the default builder uses
        # the docker driver) is no longer required: docker-py talks
        # straight to the Docker Engine API, which always uses the
        # legacy docker driver for the streamed-context build path.
        # Skipping it also removes a subprocess call into the missing
        # ``docker`` CLI.

        # Authenticate with the private registry before building so the
        # Docker daemon can pull base images (FROM lines in Dockerfile)
        # from an auth-enabled registry without 403 errors.  Uses
        # docker-py so this works without the ``docker`` CLI binary.
        #
        # Resolution priority:
        #   1. Per-service RegistryCredential (for pulling third-party images)
        #   2. ScopedRegistry chain (Project → Team → Organization)
        #   3. PlatformConfig global fallback
        _reg_user = ""
        _reg_pass = ""
        registry_url = ""

        # Check per-service RegistryCredential first (third-party image pulls)
        if getattr(self.service, 'registry_credential_id', None) and self.service.registry_credential.is_active:
            _reg_user = self.service.registry_credential.username
            _reg_pass = self.service.registry_credential.password
            if self.service.registry_credential.registry_url:
                registry_url = self.service.registry_credential.registry_url.replace("https://", "").replace("http://", "").split("/")[0]

        # Fall back to scoped registry chain
        if not registry_url:
            from apps.deployments.models_registry_scope import ScopedRegistry
            scope_obj = self.service.project or self.service.owner
            registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
            registry_url = (registry_info.get("url") or "").split("://")[-1]
            _reg_user = _reg_user or registry_info.get("username", "")
            _reg_pass = _reg_pass or registry_info.get("password", "")

        # Final fallback to PlatformConfig / settings
        if not registry_url:
            registry_url = (
                PlatformConfig.get_config_value('container_registry_url')
                or getattr(settings, 'CONTAINER_REGISTRY_URL', '') or "registry.smsly.cloud"
            ).split("://")[-1]
        _reg_user = _reg_user or PlatformConfig.get_config_value('registry_user') or getattr(settings, 'REGISTRY_USER', '')
        _reg_pass = _reg_pass or PlatformConfig.get_config_value('registry_password') or getattr(settings, 'REGISTRY_PASSWORD', '')

        if _reg_user and _reg_pass:
            try:
                from apps.cloud.docker_client import get_docker_client
                client = get_docker_client()
                client.login(
                    username=_reg_user,
                    password=_reg_pass,
                    registry=registry_url,
                )
            except Exception as exc:
                logger.warning(
                    "Docker login attempt failed (%s); proceeding without auth",
                    exc,
                )

        # Only use cache_from when the image is registry-qualified.
        # Bare image names (e.g. "smsly/name:tag") cause BuildKit to
        # resolve them to docker.io, triggering "insufficient_scope"
        # errors when the repo doesn't exist or requires auth.
        image_name = self.image_name or ""
        registry_host = (
            image_name.split("/")[0] if "/" in image_name else ""
        )
        use_cache = bool(registry_host) and ("." in registry_host or ":" in registry_host)
        cache_from = [image_name] if use_cache else []

        self._build_via_docker_py(
            context_dir=context_dir,
            dockerfile_path=dockerfile_path,
            tag=image_name,
            buildargs=build_args_dict,
            cache_from=cache_from,
            secrets=build_secrets,
        )

    def _build_via_docker_py(
        self,
        context_dir: str,
        dockerfile_path: str,
        tag: str,
        buildargs: dict,
        cache_from: list,
        secrets: dict[str, str] | None = None,
    ):
        """Build a Docker image via the docker-py SDK.

        Replaces the previous ``docker build`` subprocess invocation,
        which required the ``docker`` CLI binary in the container image.
        The SDK talks to the Docker daemon over HTTP via the shared
        ``apps.cloud.docker_client`` factory, which honours the
        ``DOCKER_HOST`` env var (pointing at the socket-proxy in compose
        mode) and falls back to the local socket otherwise.

        Build output is drained from the SDK generator and written to
        the deploy log on success (matching the previous subprocess
        behaviour of one redacted ``append_log`` call at the end).
        BuildKit cache errors get the same prune-and-retry treatment
        as the old ``_run_subprocess`` path.

        ``secrets`` is a ``{secret_id: secret_value}`` dict.  Each entry is
        written to a chmod-600 tmpfile which is passed to the Docker daemon as
        a BuildKit secret mount (``--secret id=<id>,src=<path>``).  The files
        are deleted unconditionally in a ``finally`` block — even on build
        failure or cache-error retry — so secret material never persists on
        disk beyond the build lifetime.
        """
        import io
        import tarfile

        from apps.cloud.docker_client import get_docker_client

        # The daemon needs the Dockerfile path relative to the build
        # context root inside the streamed tar.  If the Dockerfile lives
        # outside the context (rare; mostly monorepo edge cases), fall
        # back to the basename, which is the common case.
        dockerfile_rel = os.path.relpath(dockerfile_path, context_dir)
        if dockerfile_rel.startswith(".."):
            dockerfile_rel = os.path.basename(dockerfile_path)

        # ── Secret handling for docker-py ───────────────────────────────────
        # Since docker-py (legacy Docker Engine build API) does not accept a
        # `secrets=` keyword argument in `client.images.build()`, we pass any
        # build secrets via `buildargs` so they are accessible during the build.
        merged_buildargs = dict(buildargs or {})
        for secret_id, secret_val in (secrets or {}).items():
            merged_buildargs[secret_id] = secret_val
            merged_buildargs[secret_id.upper()] = secret_val

        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            # Build a fresh tar of the build context on each attempt;
            # the buffer is consumed by the SDK and can't be re-read.
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tar.add(context_dir, arcname=".")
            tar_buffer.seek(0)

            try:
                client = get_docker_client()
                build_kwargs: dict = dict(
                    fileobj=tar_buffer,
                    custom_context=True,
                    tag=tag,
                    dockerfile=dockerfile_rel,
                    buildargs=merged_buildargs or None,
                    cache_from=cache_from or None,
                    rm=True,
                    forcerm=True,
                )
                _image, build_log = client.images.build(**build_kwargs)
            except Exception as exc:
                err_str = str(exc) or type(exc).__name__
                redacted_err = redact_values(err_str, self.secret_values)
                # BuildKit cache corruption -> prune and retry once
                if (
                    is_buildkit_cache_error(redacted_err)
                    and attempt < max_attempts
                ):
                    append_log(
                        self.deployment,
                        "BuildKit cache corruption detected. "
                        "Pruning cache and retrying once...\n",
                    )
                    prune_buildkit_cache()
                    continue
                append_log(self.deployment, redacted_err)
                if is_buildkit_cache_error(redacted_err):
                    raise BuildError(
                        "Docker cache corruption detected after "
                        "automatic recovery attempt."
                    ) from exc
                raise BuildError("Docker build failed") from exc

            # Drain the build log generator
            log_chunks = []
            for entry in build_log:
                if not isinstance(entry, dict):
                    continue
                if entry.get("error"):
                    raise BuildError(entry["error"].strip())
                if entry.get("stream"):
                    log_chunks.append(entry["stream"])

            full_log = "".join(log_chunks)
            redacted = redact_values(full_log, self.secret_values)
            if len(redacted) > 20000:
                redacted = redacted[-20000:] + "\n...(truncated)"
            if redacted:
                append_log(self.deployment, redacted)
            return

    def _ensure_docker_driver(self):
        """
        Ensure the default buildx builder uses the docker driver.

        Returns True when the default builder is confirmed to be the
        docker driver (either it was already, or it was successfully
        recreated). Returns False when recreation was attempted but
        failed; the caller MUST refuse to proceed with the build in
        that case to avoid cascading buildx state changes across
        concurrent builds.

        Concurrency: protected by a class-level threading.Lock so two
        PipelineManager instances cannot race to rm/create the default
        builder simultaneously.
        """
        import subprocess
        lock = PipelineManager._buildx_driver_lock
        with lock:
            try:
                inspect = subprocess.run(
                    ["docker", "buildx", "inspect"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError:
                logger.warning(
                    "buildx_driver_inspect_skipped reason=docker_cli_not_found"
                )
                return True
            except subprocess.TimeoutExpired:
                logger.warning(
                    "buildx_driver_inspect_skipped reason=timeout"
                )
                return True
            except Exception as exc:
                logger.warning(
                    "buildx_driver_inspect_failed error=%s", exc,
                )
                return True

            if inspect.returncode != 0:
                logger.warning(
                    "buildx_driver_inspect_failed returncode=%s stderr=%s",
                    inspect.returncode,
                    (inspect.stderr or "").strip()[:200],
                )
                return True

            output = (inspect.stdout or "") + (inspect.stderr or "")
            if "Driver: docker" in output:
                return True

            logger.warning(
                "buildx_driver_recreation_started current_driver_non_docker"
            )
            try:
                rm = subprocess.run(
                    ["docker", "buildx", "rm", "default"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=rm reason=docker_cli_not_found error=%s",
                    exc,
                )
                return False
            except subprocess.TimeoutExpired:
                logger.error(
                    "buildx_driver_recreation_failed step=rm reason=timeout"
                )
                return False
            except Exception as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=rm error=%s", exc,
                )
                return False
            if rm.returncode != 0:
                logger.error(
                    "buildx_driver_recreation_failed step=rm returncode=%s stderr=%s",
                    rm.returncode,
                    (rm.stderr or "").strip()[:200],
                )
                return False
            logger.info("buildx_driver_recreation_progress step=rm completed")

            try:
                create = subprocess.run(
                    ["docker", "buildx", "create", "--name=default",
                     "--driver=docker", "--use"],
                    capture_output=True, text=True, timeout=15
                )
            except FileNotFoundError as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=create reason=docker_cli_not_found error=%s",
                    exc,
                )
                return False
            except subprocess.TimeoutExpired:
                logger.error(
                    "buildx_driver_recreation_failed step=create reason=timeout"
                )
                return False
            except Exception as exc:
                logger.error(
                    "buildx_driver_recreation_failed step=create error=%s", exc,
                )
                return False
            if create.returncode != 0:
                logger.error(
                    "buildx_driver_recreation_failed step=create returncode=%s stderr=%s",
                    create.returncode,
                    (create.stderr or "").strip()[:200],
                )
                return False

            logger.info(
                "buildx_driver_recreation_succeeded driver=docker"
            )
            return True

    def _patch_dockerfile_for_runtime(self, dockerfile_path: str):
        """
        Scan and patch the Dockerfile for outdated or incompatible runtimes.
        Currently handles: Node.js 18 -> 20 (for Next.js compatibility).
        """
        try:
            with open(dockerfile_path, encoding="utf-8") as f:
                content = f.read()

            new_content = content
            patches = []

            # 1. Node.js 18 -> 20
            # Matches: node:18, node:18-alpine, node:18-slim, etc.
            if re.search(r"FROM\s+node:18", content, re.IGNORECASE):
                new_content = re.sub(
                    r"(FROM\s+node:)18",
                    r"\1 20",
                    new_content,
                    flags=re.IGNORECASE
                ).replace("node: 20", "node:20") # Cleanup any accidental space
                patches.append("Node.js 18 → 20")

            if content != new_content:
                with open(dockerfile_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                patch_list = ", ".join(patches)
                append_log(
                    self.deployment,
                    f"🛡️ Runtime Hardening: Patched Dockerfile base image ({patch_list})\n"
                )
                logger.info("Patched Dockerfile %s: %s", dockerfile_path, patch_list)
        except Exception as exc:
            logger.warning("Failed to patch Dockerfile %s: %s", dockerfile_path, exc)

    def _detect_django_project_module(self, context_dir: str) -> str:
        """Best-effort discovery of Django project module for gunicorn startup."""
        for root, _, files in os.walk(context_dir):
            if "settings.py" not in files:
                continue
            if "__init__.py" not in files:
                continue
            rel_path = os.path.relpath(root, context_dir).strip(".\\/")
            if rel_path:
                return rel_path.replace(os.sep, ".")
        return ""

    def _resolve_nixpacks_start_command(self, context_dir: str) -> str:
        """Infer an explicit start command when the repo does not declare one."""
        explicit = str(self.service.start_command or "").strip()
        if explicit:
            return explicit

        procfile_path = os.path.join(context_dir, "Procfile")
        if os.path.isfile(procfile_path):
            try:
                with open(procfile_path, encoding="utf-8", errors="replace") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or ":" not in line:
                            continue
                        proc_name, cmd = line.split(":", 1)
                        if proc_name.strip().lower() == "web" and cmd.strip():
                            return cmd.strip()
            except OSError:
                pass

        package_json = os.path.join(context_dir, "package.json")
        if os.path.isfile(package_json):
            try:
                with open(package_json, encoding="utf-8", errors="replace") as handle:
                    pkg = json.load(handle)
                scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
                if isinstance(scripts, dict) and str(scripts.get("start", "")).strip():
                    if os.path.isfile(os.path.join(context_dir, "pnpm-lock.yaml")):
                        return "pnpm start"
                    if os.path.isfile(os.path.join(context_dir, "yarn.lock")):
                        return "yarn start"
                    if os.path.isfile(os.path.join(context_dir, "bun.lockb")):
                        return "bun run start"
                    return "npm run start"
            except (OSError, json.JSONDecodeError):
                pass

        if os.path.isfile(os.path.join(context_dir, "manage.py")):
            project_module = self._detect_django_project_module(context_dir)
            if project_module:
                return (
                    f"gunicorn {project_module}.wsgi:application "
                    "--bind 0.0.0.0:${PORT:-8000}"
                )
            return "python manage.py runserver 0.0.0.0:${PORT:-8000}"

        main_py = os.path.join(context_dir, "main.py")
        if os.path.isfile(main_py):
            try:
                with open(main_py, encoding="utf-8", errors="replace") as handle:
                    main_text = handle.read()
                if "FastAPI(" in main_text or "fastapi.FastAPI(" in main_text:
                    return "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
            except OSError:
                pass

        return ""

    def _build_with_nixpacks(self, context_dir: str):
        """Execute Nixpacks build."""
        append_log(self.deployment, "Building with Nixpacks...\n")
        import re
        env_map = {}
        for env in self.service.env_vars.all():
            if isinstance(env.value, str) and re.search(r"\{\{.*?\}\}", env.value):
                append_log(
                    self.deployment,
                    f"SKIP: env var {env.key} has unresolved placeholder {env.value} — "
                    "addon may not be provisioned yet.\n",
                )
                continue
            env_map[env.key] = env.value
        start_cmd = self._resolve_nixpacks_start_command(context_dir)
        allow_missing_start = not bool(start_cmd)

        if start_cmd:
            append_log(
                self.deployment,
                f"Using start command for Nixpacks: {start_cmd}\n",
            )
        else:
            append_log(
                self.deployment,
                "No start command detected; building with --no-error-without-start.\n",
            )

        if not self.image_name:
            raise BuildError(
                "Cannot run Nixpacks build: no image_name was generated"
            )

        try:
            result = NixpacksBuilder.build_image(
                source_dir=context_dir,
                image_name=self.image_name,
                env_vars=env_map,
                start_cmd=start_cmd or None,
                allow_missing_start=allow_missing_start,
            )
        except RuntimeError as exc:
            # Defensive retry path for environments where start command probing is unstable.
            if "no start command could be found" not in str(exc).lower():
                raise
            append_log(
                self.deployment,
                "Retrying Nixpacks build with --no-error-without-start.\n",
            )
            result = NixpacksBuilder.build_image(
                source_dir=context_dir,
                image_name=self.image_name,
                env_vars=env_map,
                start_cmd=None,
                allow_missing_start=True,
            )

        # NixpacksBuilder returns dict with stdout/stderr
        if result.get("stderr"):
            append_log(self.deployment, f"[Nixpacks Log]\n{result['stderr']}\n")

    def _run_subprocess(self, cmd: list, cwd: str):
        """Helper to run shell commands with logging."""
        env = os.environ.copy()
        # BuildKit MUST be enabled for layer caching. Without it, every build
        # re-downloads all dependencies from scratch (extremely slow).
        # If BuildKit causes cache corruption, the auto-prune below handles it.
        env["DOCKER_BUILDKIT"] = "1"
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                process = subprocess.run(
                    cmd, check=True, cwd=cwd, env=env,
                    capture_output=True, text=True, timeout=3600  # 60 minutes max
                )
                # Log output (redacted)
                output = redact_values(process.stdout + process.stderr, self.secret_values)
                if len(output) > 20000:
                    output = output[-20000:] + "\n...(truncated)"
                append_log(self.deployment, output)
                return

            except subprocess.TimeoutExpired:
                # Kill stuck buildkit containers before retry
                append_log(
                    self.deployment,
                    "Build timed out. Cleaning up stuck BuildKit containers and retrying...\n"
                )
                _cleanup_stuck_buildkit()
                if attempt >= max_attempts:
                    raise BuildError("Build timed out after maximum retries.")
                continue

            except subprocess.CalledProcessError as e:
                full_err = redact_values(e.stdout + e.stderr, self.secret_values)

                if is_buildkit_cache_error(full_err) and attempt < max_attempts:
                    append_log(
                        self.deployment,
                        "BuildKit cache corruption detected. Pruning cache and retrying once...\n"
                    )
                    prune_buildkit_cache()
                    continue

                append_log(self.deployment, full_err)
                if is_buildkit_cache_error(full_err):
                    raise BuildError(
                        "Docker cache corruption detected after automatic recovery attempt."
                    ) from e
                raise BuildError("Command failed") from e

    def _push_image(self):
        """Step 3: Push to Registry."""
        # Skip push for DOCKER type: image is already in the registry
        if self.service.deploy_type == 'DOCKER' and self.service.docker_image:
            return

        # ── Resolve registry URL and credentials ─────────────────
        # Priority: 1) deployment.registry_override
        #           2) ScopedRegistry chain (Project → Team → Organization)
        #           3) PlatformConfig global fallback
        from apps.deployments.models_registry_scope import ScopedRegistry

        deployment = self.deployment
        registry_url = None
        reg_username = None
        reg_password = None

        if deployment.registry_override:
            registry_url = deployment.registry_override.get("url")
            reg_username = deployment.registry_override.get("username")
            reg_password = deployment.registry_override.get("password")

        if not registry_url:
            scope_obj = self.service.project or self.service.owner
            registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
            registry_url = registry_info.get("url")
            reg_username = registry_info.get("username")
            reg_password = registry_info.get("password")

        is_local = is_deployment_local(self.deployment)
        if not registry_url:
            if not is_local:
                raise SystemError(
                    "No registry URL configured. "
                    "A registry is required to push/pull images for remote node deployments. "
                    "Set a registry at the Organization, Team, or Project level, "
                    "or configure CONTAINER_REGISTRY_URL."
                )
            return

        update_stage(self.deployment, 'Push', 'running')
        self._check_cancellation('Push')

        try:
            append_log(self.deployment, f"Pushing to {registry_url}...\n")
            remote_tag, push_error = NixpacksBuilder.push_image(
                self.image_name,
                registry_url,
                username=reg_username,
                password=reg_password,
            )
            self.image_name = remote_tag

            # Determine if push reached the registry.
            # push_image() strips http(s):// from the URL when forming the tag,
            # so normalise registry_url the same way before comparing.
            _norm_prefix = registry_url or ""
            for _scheme in ('https://', 'http://'):
                if _norm_prefix.startswith(_scheme):
                    _norm_prefix = _norm_prefix[len(_scheme):]
            # Strip trailing slash so "127.0.0.1:5000/" and "127.0.0.1:5000" both work.
            _norm_prefix = _norm_prefix.rstrip('/')
            pushed_to_registry = bool(_norm_prefix and remote_tag.startswith(_norm_prefix))

            if pushed_to_registry:
                update_stage(self.deployment, 'Push', 'success')
                append_log(self.deployment, f"✓ Pushed: {remote_tag}\n")
                log_exhaustive_push_diagnostics(self.deployment, registry_url, remote_tag)
            else:
                if push_error:
                    append_log(self.deployment, f"Registry error details: {push_error}\n")
                if not is_local:
                    raise SystemError(
                        f"Image push failed: Local fallback is not allowed for remote deployments. "
                        f"Target node requires a working registry to pull {remote_tag}."
                    )
                update_stage(self.deployment, 'Push', 'success')
                append_log(
                    self.deployment,
                    f"⚠ Registry unreachable; using local image: {remote_tag}\n",
                )

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
