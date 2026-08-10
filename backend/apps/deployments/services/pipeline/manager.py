import logging
import os
import re
import shutil
import tempfile
import threading

from django.conf import settings

from apps.deployments.models import Deployment, PlatformConfig
from apps.deployments.utils import (
    append_log,
    estimate_resources_from_deps,
    log_exhaustive_deployment_diagnostics,
    log_exhaustive_network_and_routing_diagnostics,
    parse_ai_resource_recommendation,
    redact_values,
    update_stage,
)
from .exceptions import PipelineError, InfraError
from .utils import _get_builds_root
from .clone import CloneMixin
from .analysis import AnalysisMixin
from .addons import AddonMixin
from .build import BuildMixin
from .compose_networking import ComposeNetworkingMixin
from .hooks import HookMixin
from .registry import RegistryMixin
from .signing import SigningMixin


logger = logging.getLogger(__name__)
class PipelineManager(
    CloneMixin, AnalysisMixin, AddonMixin, BuildMixin,
    ComposeNetworkingMixin, HookMixin, RegistryMixin, SigningMixin
):
    """
    Orchestrates the CI/CD pipeline for a deployment.
    """


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
            self._sign_image()
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
            self._sign_image()
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
        from apps.deployments.models.addons import Addon
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


    def _capture_pre_deploy_snapshot(self) -> None:
        """Capture a PRE_DEPLOY snapshot before the build starts.

        This creates a lightweight config snapshot that can be used
        to roll back to the pre-deploy state if the deployment fails.
        Snapshot failures are non-fatal (logged but not raised).
        """
        try:
            from ..snapshot_service import SnapshotService
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


    def _cleanup(self):
        """Remove temp artifacts."""
        if self.build_dir and os.path.exists(self.build_dir):
            try:
                shutil.rmtree(self.build_dir)
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Failed to cleanup build dir %s: %s", self.build_dir, e)
