import logging
import re
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from apps.deployments.models_core import Deployment
from apps.deployments.models_safedeploy import DeploymentApproval, MigrationValidation, DeploymentArtifact
from .postgres_snapshot_manager import PostgresSnapshotManager

logger = logging.getLogger(__name__)


class ProductionDeploymentPipeline:
    def process_deployment(self, deployment: Deployment) -> None:
        deployment.status = Deployment.Status.MIGRATION_PLANNING
        deployment.save()

        validation = self._get_latest_validation_for_commit(deployment.service_id, deployment.commit_hash)

        if validation:
            validation.deployment = deployment
            validation.save()

            if validation.status in [MigrationValidation.Status.FAILED, MigrationValidation.Status.INCOMPLETE]:
                logger.error(f"Blocking deployment {deployment.id}: Migration validation did not pass (Status: {validation.status})")
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return

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
                    return

        if validation and validation.requires_backup:
            self._run_backup_phase(deployment)
            if deployment.status == Deployment.Status.BACKUP_FAILED:
                return

        self._run_migration_phase(deployment)
        if deployment.status in (
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.ROLLED_BACK,
            Deployment.Status.FAILED,
        ):
            return

        self._run_tests_phase(deployment)
        if deployment.status in (
            Deployment.Status.FAILED,
            Deployment.Status.ROLLED_BACK,
        ):
            return

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        self._run_deploy_phase(deployment)
        deployment.refresh_from_db()
        if deployment.status in (
            Deployment.Status.FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.ROLLED_BACK,
        ):
            return

        self._run_health_check_phase(deployment)
        deployment.refresh_from_db()
        if deployment.status == Deployment.Status.HEALTH_CHECK_FAILED:
            return

        deployment.status = Deployment.Status.ACTIVE
        deployment.save()

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
        try:
            from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
            import tempfile, shutil
            adapter = DjangoAdapter()
            workspace_dir = tempfile.mkdtemp(prefix=f"prod_deploy_{deployment.id}_")
            repo_url = deployment.service.repository_url
            cloned_path = workspace_dir
            if repo_url:
                try:
                    from apps.deployments.services.git_manager import GitManager
                    from apps.deployments.utils import get_github_oauth_token_for_user
                    token = get_github_oauth_token_for_user(deployment.service.owner)
                    cloned_path = GitManager.clone_repo(
                        repo_url=repo_url,
                        branch=deployment.service.branch or 'main',
                        destination=workspace_dir,
                        token=token,
                        commit_hash=deployment.commit_hash
                    )
                except Exception as e:
                    logger.error(f"Migration clone failed for deployment {deployment.id}: {e}")
                    cloned_path = None
            prod_db_url = None
            for env_var in deployment.service.env_vars.all():
                if env_var.key == 'DATABASE_URL':
                    prod_db_url = env_var.value

            pre_migration_state = None
            if cloned_path and prod_db_url and adapter.detect(cloned_path):
                env = {"DATABASE_URL": prod_db_url}
                pre_migration_state = self._capture_pre_migration_state(adapter, cloned_path, env)
                if pre_migration_state:
                    meta = dict(deployment.metadata or {})
                    meta["pre_migration_state"] = pre_migration_state
                    deployment.metadata = meta
                    deployment.save(update_fields=["metadata"])
                rc, out, err = adapter.run_migrate(cloned_path, env)
                DeploymentArtifact.objects.create(service=deployment.service, deployment=deployment, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT, content=f"RC: {rc}\n{out}\n{err}")
                if rc != 0:
                    raise Exception("Production Migration apply failed.")
            shutil.rmtree(workspace_dir, ignore_errors=True)
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
        state = {}
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
        """Try to roll the production DB back to ``pre_migration_state``.

        Returns True only if every captured app was reverted successfully.
        """
        if not pre_migration_state:
            return False
        from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
        import tempfile, shutil
        adapter = DjangoAdapter()
        workspace_dir = tempfile.mkdtemp(prefix=f"prod_rollback_{deployment.id}_")
        cloned_path = workspace_dir
        repo_url = deployment.service.repository_url
        if repo_url:
            try:
                from apps.deployments.services.git_manager import GitManager
                from apps.deployments.utils import get_github_oauth_token_for_user
                token = get_github_oauth_token_for_user(deployment.service.owner)
                cloned_path = GitManager.clone_repo(
                    repo_url=repo_url,
                    branch=deployment.service.branch or 'main',
                    destination=workspace_dir,
                    token=token,
                    commit_hash=deployment.commit_hash,
                )
            except Exception as e:
                logger.error(f"Rollback clone failed for deployment {deployment.id}: {e}")
                shutil.rmtree(workspace_dir, ignore_errors=True)
                return False
        prod_db_url = None
        for env_var in deployment.service.env_vars.all():
            if env_var.key == 'DATABASE_URL':
                prod_db_url = env_var.value
        if not (cloned_path and prod_db_url and adapter.detect(cloned_path)):
            shutil.rmtree(workspace_dir, ignore_errors=True)
            return False
        env = {"DATABASE_URL": prod_db_url}
        all_ok = True
        for app_label, migration_name in pre_migration_state.items():
            try:
                rc, out, err = adapter.executor.run(
                    f"python manage.py migrate {app_label} {migration_name} --noinput",
                    cloned_path,
                    env,
                )
                DeploymentArtifact.objects.create(
                    service=deployment.service,
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
        shutil.rmtree(workspace_dir, ignore_errors=True)
        return all_ok

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
        """Run the test suite against the production DB. Opt-in via SAFEDEPLOY_RUN_TESTS."""
        if not getattr(settings, "SAFEDEPLOY_RUN_TESTS", False):
            logger.info("Skipping _run_tests_phase (SAFEDEPLOY_RUN_TESTS is not enabled).")
            return
        svc = deployment.service
        if not svc:
            return
        from apps.deployments.services.safedeploy.django_adapter import DjangoAdapter
        import tempfile, shutil
        adapter = DjangoAdapter()
        workspace_dir = tempfile.mkdtemp(prefix=f"prod_tests_{deployment.id}_")
        cloned_path = workspace_dir
        repo_url = svc.repository_url
        if repo_url:
            try:
                from apps.deployments.services.git_manager import GitManager
                from apps.deployments.utils import get_github_oauth_token_for_user
                token = get_github_oauth_token_for_user(svc.owner)
                cloned_path = GitManager.clone_repo(
                    repo_url=repo_url,
                    branch=svc.branch or 'main',
                    destination=workspace_dir,
                    token=token,
                    commit_hash=deployment.commit_hash,
                )
            except Exception as e:
                logger.error(f"Tests clone failed for deployment {deployment.id}: {e}")
                shutil.rmtree(workspace_dir, ignore_errors=True)
                deployment.status = Deployment.Status.FAILED
                deployment.save()
                return
        prod_db_url = None
        for env_var in svc.env_vars.all():
            if env_var.key == 'DATABASE_URL':
                prod_db_url = env_var.value
        if not (cloned_path and prod_db_url and adapter.detect(cloned_path)):
            shutil.rmtree(workspace_dir, ignore_errors=True)
            return
        env = {"DATABASE_URL": prod_db_url}
        try:
            rc, out, err = adapter.executor.run(
                "python manage.py test --keepdb",
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
            shutil.rmtree(workspace_dir, ignore_errors=True)

    def _run_deploy_phase(self, deployment: Deployment) -> None:
        """Recursively enqueue smart_deploy_task with skip_review=True so the
        actual build + container work runs. skip_review=True prevents the
        safedeploy pre-check at the top of smart_deploy_task from re-firing.
        """
        from apps.deployments.tasks import enqueue_smart_deploy_task
        service = deployment.service
        provider_id = str(service.provider.id) if service and service.provider else None
        result = enqueue_smart_deploy_task(
            deployment_id=str(deployment.id),
            provider_id=provider_id,
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
            from apps.deployments.services.safedeploy.health_checks import perform_health_check
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
