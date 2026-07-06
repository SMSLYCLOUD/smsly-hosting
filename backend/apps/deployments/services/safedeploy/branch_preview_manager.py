import re

from apps.deployments.models_core import Service
from apps.deployments.models_safedeploy import (
    DatabaseClone,
    MigrationValidation,
    PreviewEnvironment,
)

BRANCH_NAME_RE = re.compile(r'^[a-zA-Z0-9_./-]{1,200}$')
COMMIT_SHA_RE = re.compile(r'^[a-f0-9]{7,40}$')


class BranchPreviewManager:
    """
    Manages the lifecycle of branch preview environments.
    """

    def _validate(self, branch_name: str, commit_sha: str) -> None:
        if not isinstance(branch_name, str) or not BRANCH_NAME_RE.match(branch_name):
            raise ValueError(
                "branch_name must match ^[a-zA-Z0-9_./-]{1,200}$"
            )
        if not isinstance(commit_sha, str) or not COMMIT_SHA_RE.match(commit_sha):
            raise ValueError(
                "commit_sha must match ^[a-f0-9]{7,40}$"
            )

    def create_preview(self, service: Service, branch_name: str, commit_sha: str, user=None) -> PreviewEnvironment:
        """
        Creates a new PreviewEnvironment and begins its provisioning workflow.
        """
        self._validate(branch_name, commit_sha)
        preview = PreviewEnvironment.objects.create(
            service=service,
            branch_name=branch_name,
            commit_sha=commit_sha,
            created_by=user,
            status=PreviewEnvironment.Status.PENDING,
        )
        preview.preview_url = self.generate_preview_url(service, branch_name, preview.id.hex[:6])
        preview.save(update_fields=['preview_url', 'updated_at'])
        return preview

    def rebuild_preview(self, preview: PreviewEnvironment, commit_sha: str) -> PreviewEnvironment:
        """
        Updates the commit sha and rebuilds an existing preview.
        """
        self._validate(preview.branch_name, commit_sha)
        preview.commit_sha = commit_sha
        preview.status = PreviewEnvironment.Status.BUILDING
        preview.error_message = ""
        preview.save()
        preview.artifacts.all().update(is_archived=True)
        MigrationValidation.objects.filter(preview_environment=preview).delete()
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

        # Prefer the preview addon's own DATABASE_URL (isolated container)
        # over the clone URL (which shares the parent's DB server).
        try:
            from apps.deployments.models_addons import Addon
            preview_pg = Addon.objects.filter(
                service__name__startswith=f"preview-{preview.id.hex}",
                addon_type=Addon.Type.POSTGRES,
                status=Addon.Status.ACTIVE,
            ).first()
            if preview_pg and preview_pg.connection_url:
                env_vars['DATABASE_URL'] = preview_pg.connection_url
                return env_vars
        except Exception:
            pass

        # Fallback: use clone URL if no preview addon found (legacy path)
        try:
            has_clone = hasattr(preview, 'database_clone') and preview.database_clone
        except Exception:
            has_clone = False

        if has_clone:
            if preview.database_clone.status == DatabaseClone.Status.READY:
                env_vars['DATABASE_URL'] = preview.database_clone.clone_database_url_secret_ref

        return env_vars

    def generate_preview_url(self, service: Service, branch_name: str, unique_suffix: str = "") -> str:
        """
        Generates a safely slugified preview URL under a single subdomain level
        so wildcard TLS (e.g. *.grid.smsly.cloud) covers it.
        Example: feature-new-billing-myapp-preview.grid.smsly.cloud
        """
        safe_branch = re.sub(r'[^a-z0-9]+', '-', branch_name.lower()).strip('-')[:40]
        safe_app = re.sub(r'[^a-z0-9]+', '-', service.name.lower()).strip('-')[:30]

        try:
            base_domain = service.default_public_base_domain()
        except Exception:
            base_domain = "cloud.smsly.cloud"

        suffix = f"-{unique_suffix}" if unique_suffix else ""
        slug = f"{safe_branch}-{safe_app}{suffix}-preview"
        slug = re.sub(r'-+', '-', slug).strip('-')
        return f"https://{slug}.{base_domain}"
