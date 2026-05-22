import re
from typing import Optional
from django.utils import timezone
from apps.deployments.models_core import Service, EnvironmentVariable
from apps.deployments.models_safedeploy import PreviewEnvironment, DatabaseClone

class BranchPreviewManager:
    """
    Manages the lifecycle of branch preview environments.
    """

    def create_preview(self, service: Service, branch_name: str, commit_sha: str, user=None) -> PreviewEnvironment:
        """
        Creates a new PreviewEnvironment and begins its provisioning workflow.
        """
        preview = PreviewEnvironment.objects.create(
            service=service,
            branch_name=branch_name,
            commit_sha=commit_sha,
            created_by=user,
            status=PreviewEnvironment.Status.PENDING,
            preview_url=self.generate_preview_url(service, branch_name)
        )
        return preview

    def rebuild_preview(self, preview: PreviewEnvironment, commit_sha: str) -> PreviewEnvironment:
        """
        Updates the commit sha and rebuilds an existing preview.
        """
        preview.commit_sha = commit_sha
        preview.status = PreviewEnvironment.Status.BUILDING
        preview.error_message = ""
        preview.save()
        preview.artifacts.all().delete()
        return preview

    def destroy_preview(self, preview: PreviewEnvironment) -> bool:
        """
        Tears down the preview infrastructure and database clone.
        """
        preview.status = PreviewEnvironment.Status.DESTROYING
        preview.save()
        return True

    def inject_preview_environment_variables(self, preview: PreviewEnvironment) -> dict:
        """
        Generates and returns isolated environment variables for the preview environment.
        These are NOT saved permanently to the Service model, but passed to the container runtime.
        """
        env_vars = {
            'REDIS_PREFIX': f"preview:{preview.id}:",
            'QUEUE_PREFIX': f"preview_{preview.id}_",
            'STORAGE_PREFIX': f"previews/{preview.id}/",
        }

        if preview.preview_url:
            domain = preview.preview_url.replace("https://", "").replace("http://", "")
            env_vars['ALLOWED_HOSTS'] = domain
            env_vars['CSRF_TRUSTED_ORIGINS'] = preview.preview_url

        try:
            has_clone = hasattr(preview, 'database_clone') and preview.database_clone
        except Exception:
            has_clone = False

        if has_clone:
            if preview.database_clone.status == DatabaseClone.Status.READY:
                env_vars['DATABASE_URL'] = preview.database_clone.clone_database_url_secret_ref

        return env_vars

    def generate_preview_url(self, service: Service, branch_name: str) -> str:
        """
        Generates a safely slugified preview URL.
        Example: feature-new-billing-system--myapp.preview.domain.com
        """
        safe_branch = re.sub(r'[^a-z0-9]+', '-', branch_name.lower()).strip('-')
        safe_app = re.sub(r'[^a-z0-9]+', '-', service.name.lower()).strip('-')

        try:
            base_domain = service.default_public_base_domain()
        except Exception:
            base_domain = "cloud.smsly.cloud"

        return f"https://{safe_branch}--{safe_app}.preview.{base_domain}"
