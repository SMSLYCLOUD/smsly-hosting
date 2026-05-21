from celery import shared_task
from django.contrib.auth import get_user_model
import logging
from services.code_intelligence import analyze_codebase_chunked
from services.ecosystem import _github_headers

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task(bind=True)
def deep_scan_and_verify_task(self, user_id, repos_data, deploy_plan, ai_provider=None):
    """
    Background task to perform a deep codebase scan and cross-verify with the deployment plan.
    """
    try:
        user = User.objects.get(id=user_id)
        
        # Get github token
        github_token = None
        try:
            from apps.deployments.views_analysis import RepoAnalysisView
            # Helper to get token
            view = RepoAnalysisView()
            github_token = view._get_github_access_token(user)
        except Exception as e:
            logger.warning(f"Could not retrieve github token: {e}")
            
        self.update_state(state='PROGRESS', meta={'state': 'Starting deep codebase scan...'})
        
        result = analyze_codebase_chunked(
            repos_data=repos_data,
            deploy_plan=deploy_plan,
            github_token=github_token,
            ai_provider=ai_provider
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Deep scan task failed: {str(e)}", exc_info=True)
        return {"error": str(e)}
