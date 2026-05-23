import logging
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

            if validation.requires_manual_approval:
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
        if deployment.status == Deployment.Status.MIGRATION_FAILED:
            return

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        deployment.status = Deployment.Status.ACTIVE
        deployment.save()

    def _get_latest_validation_for_commit(self, service_id, commit_hash):
        qs = MigrationValidation.objects.filter(
            models.Q(preview_environment__service_id=service_id, preview_environment__commit_sha=commit_hash)
            | models.Q(deployment__service_id=service_id, deployment__commit_hash=commit_hash)
        ).order_by('-created_at')
        return qs.first()

    def _run_backup_phase(self, deployment: Deployment) -> None:
        logger.info(f"Backup phase not yet implemented for deployment {deployment.id}")

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

            if cloned_path and prod_db_url and adapter.detect(cloned_path):
                env = {"DATABASE_URL": prod_db_url}
                rc, out, err = adapter.run_migrate(cloned_path, env)
                DeploymentArtifact.objects.create(service=deployment.service, deployment=deployment, artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_OUTPUT, content=f"RC: {rc}\n{out}\n{err}")
                if rc != 0:
                    raise Exception("Production Migration apply failed.")
            shutil.rmtree(workspace_dir, ignore_errors=True)
        except Exception as e:
            deployment.status = Deployment.Status.MIGRATION_FAILED
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
