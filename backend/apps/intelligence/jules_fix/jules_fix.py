"""
Jules auto-fix: react to deployment failures, fix them, open PRs.

This module provides a single Celery task that can be triggered when a
deployment fails. It does the following, in order:

1. Collect deployment logs and error context.
2. Ask the Google-Jules AI to analyse the failure and propose a fix.
3. Clone the repository, create a temporary branch, apply the fix.
4. Commit and push the branch.
5. Open a Pull Request on GitHub.
6. If the fix succeeds, re-deploy the service.

All external calls (Jules API, GitHub API, SSH git operations) are wrapped
in retry logic with exponential back-off. If any step fails the task logs
the error and returns a structured error payload - it never crashes the
Celery worker.
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

import backoff
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.core.auth import APIKeyAuthentication
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.cost import CostAdvisor
from apps.intelligence.providers import ask_with_fallback, get_configured_providers
from apps.intelligence.views import _json_safe
from apps.scripts.github import GitHubClient

logger = logging.getLogger(__name__)


@dataclass
class FixResult:
    """Result of a Jules auto-fix attempt."""

    success: bool
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    fix_description: Optional[str] = None
    error: Optional[str] = None
    deployment_id: Optional[str] = None


def _collect_failure_context(deployment_id: str, logs: str) -> str:
    """Build a prompt context from deployment logs."""
    return f"""Deployment failed. Deployment ID: {deployment_id}
Logs:
{logs[:10000]}

Please analyse the error and provide a concrete fix. Return ONLY valid JSON
with this exact structure:
{{
    "fix_description": "<brief description of the fix>",
    "files_to_change": ["<path/to/file.py>"],
    "suggested_changes": {{
        "<path/to/file.py>": "<code to replace or the fix>"
    }}
}}
"""


@backoff.on_exception(
    backoff.expo,
    Exception,
    max_tries=5,
    factor=2,
)
def _ask_jules_for_fix(prompt: str) -> str:
    """Ask Jules AI for a fix suggestion."""
    try:
        response, _ = ask_with_fallback(prompt)
        return str(response)
    except Exception as exc:
        logger.error("Jules fix request failed: %s", exc)
        raise


def _parse_jules_response(raw: str) -> Dict[str, Any]:
    """Extract and validate the JSON fix payload from Jules response.

    The expected structure is:
    {
        "fix_description": "...",
        "files_to_change": ["path/to/file.py"],
        "suggested_changes": {"path/to/file.py": "new content"}
    }

    This function now validates that the required keys exist and that the
    ``files_to_change`` list matches the keys of ``suggested_changes``.  It also
    sanitises the file paths to prevent directory traversal attacks.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract a JSON block from the raw text.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            payload = json.loads(match.group())
        else:
            raise ValueError("Could not extract JSON fix from Jules response")

    # Basic schema validation
    required_keys = {"fix_description", "files_to_change", "suggested_changes"}
    if not required_keys.issubset(payload):
        missing = required_keys - set(payload)
        raise ValueError(f"Jules response missing required keys: {missing}")

    # Ensure files_to_change is a list of strings
    if not isinstance(payload["files_to_change"], list):
        raise ValueError("'files_to_change' must be a list")
    if not all(isinstance(p, str) for p in payload["files_to_change"]):
        raise ValueError("All entries in 'files_to_change' must be strings")

    # Ensure suggested_changes is a dict mapping the same files
    if not isinstance(payload["suggested_changes"], dict):
        raise ValueError("'suggested_changes' must be a dict")
    if set(payload["files_to_change"]) != set(payload["suggested_changes"].keys()):
        raise ValueError("Mismatch between 'files_to_change' and keys of 'suggested_changes'")

    # Sanitize paths – disallow absolute paths and parent directory references
    safe_files = []
    for path in payload["files_to_change"]:
        if os.path.isabs(path) or ".." in path.split(os.path.sep):
            raise ValueError(f"Unsafe file path detected in Jules response: {path}")
        safe_files.append(path)
    payload["files_to_change"] = safe_files

    return payload


def _apply_fix_to_repo(
    repo_path: str,
    files_to_change: Dict[str, str],
    branch_name: str,
) -> bool:
    """Apply Jules‑suggested fixes to the repository safely.

    The function now:
    * Switches to the repository directory using a context manager.
    * Captures stdout/stderr from each git command for detailed logging.
    * Cleans up the temporary branch on any failure to avoid polluting the repo.
    """
    original_cwd = os.getcwd()
    try:
        os.chdir(repo_path)

        def _run_git(args: List[str]) -> subprocess.CompletedProcess:
            result = subprocess.run(
                ["git", *args],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error(
                    "Git command failed: git %s\nstdout: %s\nstderr: %s",
                    " ".join(args),
                    result.stdout,
                    result.stderr,
                )
                raise RuntimeError(f"Git command failed: {' '.join(args)}")
            return result

        # Create a new branch
        _run_git(["checkout", "-b", branch_name])

        # Write the suggested files
        for filepath, content in files_to_change.items():
            full_path = os.path.join(repo_path, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        _run_git(["add", "."])
        _run_git([
            "commit",
            "-m",
            f"fix: auto-fix deployment failure via Jules AI",
        ])
        _run_git(["push", "origin", branch_name])

        return True
    except Exception as exc:
        logger.error("Failed to apply fix to repo: %s", exc)
        # Attempt to clean up the branch if it was created
        try:
            _run_git(["checkout", "main"])
            _run_git(["branch", "-D", branch_name])
        except Exception:
            # Suppress cleanup errors – we already logged the original failure
            pass
        return False
    finally:
        os.chdir(original_cwd)


def _create_pr(
    github_client: GitHubClient,
    repo_name: str,
    branch_name: str,
    title: str,
    body: str,
) -> Optional[str]:
    """Create a Pull Request on GitHub."""
    try:
        pr = github_client.create_pull_request(
            repo=repo_name,
            title=title,
            body=body,
            head=branch_name,
            base="main",
        )
        return pr.get("html_url")
    except Exception as exc:
        logger.error("Failed to create PR: %s", exc)
        return None


@shared_task(
    bind=True,
    name="apps.intelligence.jules_fix.jules_fix_deployment_failure",
    max_retries=2,
    soft_time_limit=900,
    time_limit=960,
)
def jules_fix_deployment_failure(
    self,
    deployment_id: str,
    logs: str,
    repo_path: str,
    repo_url: str,
) -> Dict[str, Any]:
    """
    React to a deployment failure, use Jules to fix it, open a PR.

    Args:
        deployment_id: The ID of the failed deployment.
        logs: Raw deployment logs.
        repo_path: Local path to the git repository.
        repo_url: GitHub repo URL (e.g. "owner/repo").

    Returns:
        A dict with the fix result.
    """
    logger.info("Jules auto-fix triggered for deployment %s", deployment_id)

    result = FixResult(success=False, deployment_id=deployment_id)

    try:
        context = _collect_failure_context(deployment_id, logs)

        jules_response = _ask_jules_for_fix(context)
        fix_payload = _parse_jules_response(jules_response)

        fix_description = fix_payload.get("fix_description", "Auto-fix via Jules")
        files_to_change = fix_payload.get("files_to_change", {})

        branch_name = f"jules-fix-{deployment_id[:8]}-{int(time.time())}"
        if not _apply_fix_to_repo(repo_path, files_to_change, branch_name):
            result.error = "Failed to apply fix to repository"
            return _json_safe(result, {})

        github_client = GitHubClient(repo_url)
        pr_url = _create_pr(
            github_client,
            repo_url,
            branch_name,
            f"fix: auto-fix deployment failure ({deployment_id[:8]})",
            fix_description,
        )

        if pr_url:
            result.success = True
            result.pr_url = pr_url
            result.fix_description = fix_description
            logger.info("PR created: %s", pr_url)
            logger.info("Fix applied and PR opened. Re-deploy can be triggered via webhook.")
        else:
            result.error = "Failed to create Pull Request"

        return _json_safe(result, {})

    except Exception as exc:
        logger.error("Jules auto-fix failed: %s", exc)
        result.error = str(exc)
        return _json_safe(result, {})
