import logging

logger = logging.getLogger(__name__)

from celery import shared_task
from django.contrib.auth import get_user_model
from apps.deployments.services.code_intelligence import analyze_codebase_chunked

User = get_user_model()


@shared_task(bind=True, soft_time_limit=600, time_limit=660)
def deep_scan_and_verify_task(self, user_id, repos_data, deploy_plan, ai_provider=None):
    """
    Background task to perform a deep codebase scan and cross-verify with the deployment plan.
    """
    try:
        user = User.objects.get(id=user_id)

        try:
            from apps.deployments.models import Service
            owned_repo_ids = {
                str(sid) for sid in Service.objects.filter(
                    owner=user
                ).values_list('id', flat=True)
            }
            owned_repo_urls = {
                url for url in Service.objects.filter(
                    owner=user
                ).values_list('repository_url', flat=True)
                if url
            }
        except Exception as e:
            logger.error("Could not load ownership for %s: %s", user_id, e)
            return {"error": f"ownership check failed: {e}"}

        from apps.deployments.tasks.ecosystem import _repository_url

        safe_repos = []
        for repo in repos_data:
            if not isinstance(repo, dict):
                continue
            owner_id = repo.get('owner_id')
            if owner_id and owner_id != user_id:
                logger.warning(
                    "Dropping repo %s from deep scan: not owned by user %s",
                    repo.get('id') or repo.get('repo'),
                    user_id,
                )
                continue
            if owner_id is None:
                repo_id = str(repo.get('id') or repo.get('repo_id') or '')
                repo_url = repo.get('repo') or repo.get('html_url') or repo.get('url') or ''
                owned = False
                if repo_id and repo_id in owned_repo_ids:
                    owned = True
                if repo_url:
                    normalized = _repository_url(repo_url)
                    if normalized in owned_repo_urls:
                        owned = True
                if not owned:
                    logger.debug(
                        "Repo %s not in user's deployed services; proceeding anyway (will be cloned from GitHub)",
                        repo_id or repo_url,
                    )
            safe_repos.append(repo)

        if not safe_repos:
            logger.info("deep_scan_and_verify_task: no owned repos for user %s; skipping LLM call", user_id)
            return {
                "global_overview": "",
                "verification": {
                    "is_valid": True,
                    "missing_env_vars": [],
                    "architectural_warnings": [],
                    "skipped": True,
                },
            }

        github_token = None
        try:
            from apps.cloud.views_analysis import RepoAnalysisView
            view = RepoAnalysisView()
            github_token = view._get_github_access_token(user)
        except Exception as e:
            logger.warning(f"Could not retrieve github token: {e}")

        self.update_state(state='PROGRESS', meta={'state': 'Starting deep codebase scan...'})

        result = analyze_codebase_chunked(
            repos_data=safe_repos,
            deploy_plan=deploy_plan,
            github_token=github_token,
            ai_provider=ai_provider
        )

        return result

    except Exception as e:
        logger.error(f"Deep scan task failed: {e!s}", exc_info=True)
        return {"error": str(e)}
