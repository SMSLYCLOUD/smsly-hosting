"""Git module."""
import os
import shutil
import git
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class GitManager:
    """
    Manages Git operations: Cloning and Checkout.
    """

    @staticmethod
    def clone_repo(repo_url: str, branch: str = 'main',
                   destination: str = '/tmp/builds', token: str = None) -> str:
        """
        Clones a repository to a destination.
        Returns the path to the cloned directory.
        """

        # Prepare Auth URL if token provided
        if token:
            parsed = urlparse(repo_url)
            # Inject token: https://oauth2:TOKEN@github.com/...
            authed_url = parsed._replace(
                netloc=f"oauth2:{token}@{parsed.netloc}").geturl()
        else:
            authed_url = repo_url

        # Clean destination
        repo_dir = os.path.join(
            destination, repo_url.split('/')[-1].replace('.git', ''))
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

        os.makedirs(repo_dir, exist_ok=True)

        logger.info(f"Cloning {repo_url} (branch: {branch}) to {repo_dir}")

        try:
            git.Repo.clone_from(authed_url, repo_dir, branch=branch, depth=1)
            return repo_dir
        except git.GitCommandError as e:
            logger.error(f"Git Clone Failed: {e}")
            raise RuntimeError(f"Failed to clone repository: {e}")
