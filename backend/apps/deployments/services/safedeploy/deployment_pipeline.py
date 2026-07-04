import logging
import os
import re


def _get_service_db_url(svc) -> str | None:
    db_url = None
    for env_var in svc.env_vars.all():
        if env_var.key in ('DATABASE_URL', 'POSTGRES_URL', 'DB_URL', 'DIRECT_DATABASE_URL', 'SQLALCHEMY_DATABASE_URI'):
            db_url = env_var.value
            break
        if not db_url and str(env_var.value).startswith('postgresql://'):
            db_url = env_var.value
    return db_url

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.deployments.models_core import Deployment
from apps.deployments.models_safedeploy import (
    DeploymentApproval,
    DeploymentArtifact,
    MigrationValidation,
)

logger = logging.getLogger(__name__)


class ProductionDeploymentPipeline:
    _TERMINAL_STATUSES = (
        Deployment.Status.ACTIVE,
        Deployment.Status.FAILED,
        Deployment.Status.CANCELLED,
        Deployment.Status.ROLLED_BACK,
    )

    def process_deployment(self, deployment: Deployment) -> Deployment:
        """Run a deployment through the full pipeline. Idempotent: if the
        deployment is already in a terminal status (``ACTIVE``,
        ``FAILED``, ``CANCELLED`` or ``ROLLED_BACK``), the call
        short-circuits and returns the existing deployment row.
        """
        deployment.refresh_from_db()
        if deployment.status in self._TERMINAL_STATUSES:
            logger.info(
                "process_deployment: deployment %s is already in terminal "
                "status %s; short-circuiting (idempotent no-op).",
                deployment.id, deployment.status,
            )
            return deployment

        deployment.status = Deployment.Status.MIGRATION_PLANNING
        deployment.save()

        validation = self._get_latest_validation_for_commit(deployment.service.id, deployment.commit_hash)

        if validation:
            validation.deployment = deployment
            validation.save()

            if validation.status in [MigrationValidation.Status.FAILED, MigrationValidation.Status.INCOMPLETE]:
                logger.error(f"Blocking deployment {deployment.id}: Migration validation did not pass (Status: {validation.status})")
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return deployment

            needs_manual_approval = (
                validation.auto_deploy_policy == MigrationValidation.AutoDeployPolicy.NEVER
                or validation.risk_level in [
                    MigrationValidation.RiskLevel.HIGH,
                    MigrationValidation.RiskLevel.CRITICAL,
                ]
            )
            if needs_manual_approval:
                approval = DeploymentApproval.objects.filter(deployment=deployment, status=DeploymentApproval.Status.APPROVED).first()
                if not approval:
                    deployment.status = Deployment.Status.AWAITING_APPROVAL
                    deployment.save()
                    return deployment

        if validation and validation.requires_backup:
            self._run_backup_phase(deployment)
            if deployment.status == Deployment.Status.BACKUP_FAILED:
                return deployment

        self._run_migration_phase(deployment)
        if deployment.status in (
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.ROLLED_BACK,
            Deployment.Status.FAILED,
        ):
            return deployment

        self._run_tests_phase(deployment)
        if deployment.status in (
            Deployment.Status.FAILED,
            Deployment.Status.ROLLED_BACK,
        ):
            return deployment

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        self._run_deploy_phase(deployment)
        deployment.refresh_from_db()
        if deployment.status in (
            Deployment.Status.FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.ROLLED_BACK,
        ):
            return deployment

        self._run_health_check_phase(deployment)
        deployment.refresh_from_db()
        if deployment.status == Deployment.Status.HEALTH_CHECK_FAILED:
            return deployment

        deployment.status = Deployment.Status.ACTIVE
        deployment.save()
        return deployment

    def _get_latest_validation_for_commit(self, service_id, commit_hash):
        qs = MigrationValidation.objects.filter(
            models.Q(preview_environment__service_id=service_id, preview_environment__commit_sha=commit_hash)
            | models.Q(deployment__service_id=service_id, deployment__commit_hash=commit_hash)
        ).order_by('-created_at')
        return qs.first()

    def _run_backup_phase(self, deployment: Deployment) -> None:
        """Create a pre-migration service backup for rollback safety."""
        from apps.deployments.services.backup_service import BackupService
        svc = deployment.service
        if not svc:
            logger.warning("Backup phase skipped — no service attached to deployment %s", deployment.id)
            return
        try:
            backup_svc = BackupService()
            backup_svc.backup_service(svc.id, backup_type='PRE_DEPLOY')
            logger.info("Pre-deploy backup created for service %s (deployment %s)", svc.name, deployment.id)
        except Exception as exc:
            logger.warning("Backup phase failed for deployment %s: %s", deployment.id, exc)

    def _run_migration_phase(self, deployment: Deployment) -> None:
        deployment.status = Deployment.Status.MIGRATION_RUNNING
        deployment.save()
        pre_migration_state = None
        workspace_dir = None
        try:
            import shutil
            import tempfile

            svc = deployment.service
            repo_url = svc.repository_url
            if not repo_url:
                logger.warning("Migration phase skipped — no repository URL for service %s", svc.id)
                return

            workspace_dir = tempfile.mkdtemp(prefix=f"prod_deploy_{deployment.id}_")

            from apps.deployments.utils import get_github_oauth_token_for_user
            token = get_github_oauth_token_for_user(svc.owner)

            from apps.deployments.services.git_manager import GitManager
            cloned_path = GitManager.clone_repo(
                repo_url=repo_url,
                branch=svc.branch or 'main',
                destination=workspace_dir,
                token=token,
                commit_hash=deployment.commit_hash,
            )
            if not cloned_path:
                raise Exception("Failed to clone repository for migration phase")

            prod_db_url = _get_service_db_url(svc)

            if not prod_db_url:
                raise Exception("No DATABASE_URL configured on service")

            service_env_vars = {
                env_var.key: env_var.value
                for env_var in svc.env_vars.all()
            }

            from apps.deployments.services.safedeploy.migration_environment import (
                build_migration_environment,
            )
            mig_env = build_migration_environment(cloned_path, prod_db_url, service_env_vars, block_addon_urls=False)
            if not mig_env.ok:
                raise Exception(f"Migration environment setup failed: {mig_env.error}")

            from apps.deployments.services.safedeploy.django_adapter import (
                DjangoAdapter,
            )
            adapter = DjangoAdapter(python_bin=mig_env.python_bin)

            if not adapter.detect(cloned_path):
                raise Exception("manage.py not found in cloned repository")

            env = mig_env.env
            pre_migration_state = self._capture_pre_migration_state(adapter, cloned_path, env)
            if pre_migration_state:
                meta = dict(deployment.metadata or {})
                meta["pre_migration_state"] = pre_migration_state
                deployment.metadata = meta
                deployment.save(update_fields=["metadata"])

            rc, out, err = adapter.run_migrate(cloned_path, env)
            DeploymentArtifact.objects.create(
                service=svc,
                deployment=deployment,
                artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT,
                content=f"RC: {rc}\n{out}\n{err}",
            )
            if rc != 0:
                raise Exception(f"Production Migration apply failed: {err[-500:] if err else 'unknown'}")
        except Exception as e:
            logger.error(f"Migration phase failed for deployment {deployment.id}: {e}")
            deployment.status = Deployment.Status.MIGRATION_FAILED
            deployment.save()
            rolled_back = self._attempt_migration_rollback(deployment, pre_migration_state)
            if rolled_back:
                deployment.status = Deployment.Status.ROLLED_BACK
                deployment.save()
                logger.warning("Migration rolled back for deployment %s", deployment.id)
            else:
                self._emit_critical_rollback_alert(deployment, e)
                deployment.status = Deployment.Status.FAILED
                deployment.save()
        finally:
            if workspace_dir:
                shutil.rmtree(workspace_dir, ignore_errors=True)

    def _capture_pre_migration_state(self, adapter, cloned_path, env):
        """Run `manage.py showmigrations --plan` and extract the last applied migration per app.

        Returns a dict like ``{"auth": "0012_alter_user_first_name_max_length", ...}``,
        or ``None`` if the command could not be executed or parsed.
        """
        rc, out, err = adapter.run_showmigrations(cloned_path, env)
        if rc != 0:
            logger.warning("showmigrations --plan failed: rc=%s stderr=%s", rc, err)
            return None
        return self._parse_showmigrations_plan(out)

    def _parse_showmigrations_plan(self, output: str):
        """Parse `manage.py showmigrations --plan` output.

        Each non-empty line looks like ``[X] app_label.migration_name`` or
        ``[ ] app_label.migration_name``.  We keep the most recent applied
        (marked ``[X]``) migration per app, which is the target for an
        automatic rollback.
        """
        state: dict[str, str] = {}
        pattern = re.compile(r"\[\s*([X x])\s*\]\s+([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\s*$")
        for raw_line in (output or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = pattern.match(line)
            if not match:
                continue
            applied_marker, app_label, migration_name = match.groups()
            if applied_marker.strip().lower() == "x":
                state.setdefault(app_label, migration_name)
        return state or None

    def _attempt_migration_rollback(self, deployment: Deployment, pre_migration_state) -> bool:
        if not pre_migration_state:
            return False
        import shutil
        import tempfile

        workspace_dir = None
        try:
            workspace_dir = tempfile.mkdtemp(prefix=f"prod_rollback_{deployment.id}_")
            svc = deployment.service
            repo_url = svc.repository_url
            if not repo_url:
                return False

            from apps.deployments.utils import get_github_oauth_token_for_user
            token = get_github_oauth_token_for_user(svc.owner)

            from apps.deployments.services.git_manager import GitManager
            cloned_path = GitManager.clone_repo(
                repo_url=repo_url,
                branch=svc.branch or 'main',
                destination=workspace_dir,
                token=token,
                commit_hash=deployment.commit_hash,
            )
            if not cloned_path:
                return False

            prod_db_url = _get_service_db_url(svc)
            if not prod_db_url:
                return False

            service_env_vars = {
                env_var.key: env_var.value
                for env_var in svc.env_vars.all()
            }

            from apps.deployments.services.safedeploy.migration_environment import (
                build_migration_environment,
            )
            mig_env = build_migration_environment(cloned_path, prod_db_url, service_env_vars, block_addon_urls=False)
            if not mig_env.ok:
                logger.error("Rollback venv setup failed: %s", mig_env.error)
                return False

            from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
            adapter = DjangoAdapter(python_bin=mig_env.python_bin)
            if not adapter.detect(cloned_path):
                return False

            env = mig_env.env
            all_ok = True
            for app_label, migration_name in pre_migration_state.items():
                try:
                    rc, out, err = adapter.executor.run(
                        f"{mig_env.python_bin} manage.py migrate {app_label} {migration_name} --noinput",
                        cloned_path,
                        env,
                    )
                    DeploymentArtifact.objects.create(
                        service=svc,
                        deployment=deployment,
                        artifact_type=DeploymentArtifact.ArtifactType.ROLLBACK_REPORT,
                        content=f"Rollback to {app_label}.{migration_name}: rc={rc}\n{out}\n{err}",
                    )
                    if rc != 0:
                        all_ok = False
                        logger.error(
                            "Rollback migrate %s %s failed: rc=%s stderr=%s",
                            app_label, migration_name, rc, err,
                        )
                except Exception as e:
                    all_ok = False
                    logger.error("Rollback for %s.%s raised: %s", app_label, migration_name, e)
            return all_ok
        except Exception as e:
            logger.error("Rollback attempt failed: %s", e)
            return False
        finally:
            if workspace_dir:
                shutil.rmtree(workspace_dir, ignore_errors=True)

    def _emit_critical_rollback_alert(self, deployment: Deployment, original_error) -> None:
        """Best-effort critical alert when migration rollback cannot recover."""
        message = (
            f"CRITICAL: migration rollback failed for deployment {deployment.id} "
            f"on service {deployment.service.name if deployment.service else 'n/a'}: {original_error}"
        )
        logger.critical(message)
        try:
            from apps.notifications.tasks import notify_deploy_event
            owner_id = getattr(getattr(deployment, "service", None), "owner_id", None)
            if owner_id:
                notify_deploy_event(
                    user_id=owner_id,
                    service_name=deployment.service.name,
                    status="CRITICAL_ROLLBACK_FAILED",
                    commit_hash=deployment.commit_hash,
                    error=str(original_error),
                )
        except Exception as alert_err:
            logger.error("Failed to emit critical rollback alert: %s", alert_err)

    def _run_tests_phase(self, deployment: Deployment) -> None:
        if not getattr(settings, "SAFEDEPLOY_RUN_TESTS", False):
            logger.info("Skipping _run_tests_phase (SAFEDEPLOY_RUN_TESTS is not enabled).")
            return
        svc = deployment.service
        if not svc:
            return
        import shutil
        import tempfile

        workspace_dir = None
        try:
            workspace_dir = tempfile.mkdtemp(prefix=f"prod_tests_{deployment.id}_")
            repo_url = svc.repository_url
            if not repo_url:
                return

            from apps.deployments.utils import get_github_oauth_token_for_user
            token = get_github_oauth_token_for_user(svc.owner)

            from apps.deployments.services.git_manager import GitManager
            cloned_path = GitManager.clone_repo(
                repo_url=repo_url,
                branch=svc.branch or 'main',
                destination=workspace_dir,
                token=token,
                commit_hash=deployment.commit_hash,
            )
            if not cloned_path:
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return

            prod_db_url = _get_service_db_url(svc)
            if not prod_db_url:
                return

            service_env_vars = {
                env_var.key: env_var.value
                for env_var in svc.env_vars.all()
            }

            from apps.deployments.services.safedeploy.migration_environment import (
                build_migration_environment,
            )
            mig_env = build_migration_environment(cloned_path, prod_db_url, service_env_vars, block_addon_urls=False)
            if not mig_env.ok:
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return

            from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
            adapter = DjangoAdapter(python_bin=mig_env.python_bin)
            if not adapter.detect(cloned_path):
                return

            env = mig_env.env
            rc, out, err = adapter.executor.run(
                f"{mig_env.python_bin} manage.py test --keepdb",
                cloned_path,
                env,
                timeout=600,
            )
            DeploymentArtifact.objects.create(
                service=svc,
                deployment=deployment,
                artifact_type=DeploymentArtifact.ArtifactType.TEST_OUTPUT,
                content=f"RC: {rc}\n{out}\n{err}",
            )
            if rc != 0:
                deployment.status = Deployment.Status.FAILED
                deployment.save()
        except Exception as e:
            logger.error(f"Tests phase failed for deployment {deployment.id}: {e}")
            deployment.status = Deployment.Status.FAILED
            deployment.save()
        finally:
            if workspace_dir:
                shutil.rmtree(workspace_dir, ignore_errors=True)

    def _run_deploy_phase(self, deployment: Deployment) -> None:
        """Recursively enqueue smart_deploy_task with skip_review=True so the
        actual build + container work runs. skip_review=True prevents the
        safedeploy pre-check at the top of smart_deploy_task from re-firing.
        """
        from apps.deployments.tasks_deploy import enqueue_smart_deploy_task
        service = deployment.service
        provider_id = str(service.provider.id) if service and service.provider else None
        result = enqueue_smart_deploy_task(
            deployment_id=str(deployment.id),
            provider_id=provider_id or "",  # type: ignore[arg-type]
            skip_review=True,
        )
        if result is not None and hasattr(result, "get"):
            try:
                result.get(timeout=3600, propagate=True)
            except Exception as e:
                logger.error(f"Deploy phase raised for deployment {deployment.id}: {e}")

    def _run_health_check_phase(self, deployment: Deployment) -> None:
        """Hit the service's public URL on the configured health path. Skip if no public domain."""
        svc = deployment.service
        if not svc:
            return
        public_url = (svc.public_domain or "").strip()
        if not public_url:
            logger.info("Skipping health check for deployment %s: no public_domain set", deployment.id)
            return
        if not public_url.startswith("http://") and not public_url.startswith("https://"):
            public_url = f"https://{public_url}"
        health_path = (svc.health_check_path or "/health").strip() or "/health"
        if not health_path.startswith("/"):
            health_path = f"/{health_path}"
        full_url = f"{public_url.rstrip('/')}{health_path}"
        try:
            from apps.deployments.services.safedeploy.health_checks import (
                perform_health_check,
            )
            ok, _ = perform_health_check(full_url)
        except Exception as e:
            logger.error(f"Health check raised for deployment {deployment.id}: {e}")
            ok = False
        if not ok:
            deployment.status = Deployment.Status.HEALTH_CHECK_FAILED
            deployment.save()

    @transaction.atomic
    def approve_deployment(self, deployment: Deployment, user) -> DeploymentApproval:
        approval, _ = DeploymentApproval.objects.get_or_create(service=deployment.service, deployment=deployment)
        approval.status = DeploymentApproval.Status.APPROVED
        approval.approved_by = user
        approval.rejected_by = None
        approval.approved_at = timezone.now()
        validation = getattr(deployment, 'migration_validation', None)
        if validation:
            approval.risk_level = validation.risk_level
        approval.save()
        deployment.status = Deployment.Status.MIGRATION_PLANNING
        deployment.save()
        return approval

    def approve_and_process(self, deployment: Deployment, user) -> DeploymentApproval:
        approval = self.approve_deployment(deployment, user)
        self.process_deployment(deployment)
        return approval

    @transaction.atomic
    def reject_deployment(self, deployment: Deployment, user, notes: str = "") -> DeploymentApproval:
        approval, created = DeploymentApproval.objects.get_or_create(service=deployment.service, deployment=deployment)
        if not created:
            approval.approved_by = approval.approved_by
        approval.status = DeploymentApproval.Status.REJECTED
        approval.rejected_by = user
        approval.rejected_at = timezone.now()
        approval.approval_notes = notes
        approval.save()
        deployment.status = Deployment.Status.CANCELLED
        deployment.save()
        return approval
