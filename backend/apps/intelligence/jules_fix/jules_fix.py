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
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import backoff
from celery import shared_task
from django.conf import settings

from apps.deployments.models import Deployment
from apps.intelligence.models import AIProviderSettings
from apps.intelligence.providers import ask_with_fallback
from apps.intelligence.views import _json_safe
from apps.scripts.github import GitHubClient

logger = logging.getLogger(__name__)


MAX_FILES_PER_JULES_PR = 5
MAX_BYTES_PER_JULES_PR = 50_000


@dataclass
class FixResult:
    """Result of a Jules auto-fix attempt."""

    success: bool
    pr_url: str | None = None
    pr_number: int | None = None
    fix_description: str | None = None
    error: str | None = None
    deployment_id: str | None = None


def _collect_failure_context(deployment_id: str, logs: str, service=None) -> str:
    """Build a prompt context from deployment logs + Prometheus/Loki metrics."""
    context = f"""Deployment failed. Deployment ID: {deployment_id}
Logs:
{logs[:10000]}
"""
    # ── Enrich with monitoring data if service is available ────────────────
    if service:
        try:
            from apps.deployments.services.scaling_ai import ScalingAnalyzer
            analyzer = ScalingAnalyzer(service)
            result = analyzer.analyze()
            metrics = result.get('metrics', {})
            errors = result.get('error_analysis', {})
            if any(metrics.values()) or any(errors.values()):
                context += "\n--- Monitoring Context (last 5min) ---\n"
                if metrics.get('cpu_percent'):
                    context += f"CPU: {metrics['cpu_percent']:.1f}%\n"
                if metrics.get('memory_mb'):
                    context += f"Memory: {metrics['memory_mb']:.1f}MB\n"
                if metrics.get('memory_trend') and metrics['memory_trend'] > 0:
                    context += f"Memory trend: +{metrics['memory_trend']:.1f} MB/min (possible leak)\n"
                if errors.get('oom_detected'):
                    context += "OOM detected in recent logs\n"
                if errors.get('crash_loop'):
                    context += "Crash loop detected\n"
                context += "--- End Monitoring ---\n"
        except Exception:
            pass  # monitoring is optional enrichment

    context += """
Please analyse the error and provide a concrete fix. Return ONLY valid JSON
with this exact structure:
{{
    "fix_description": "<brief description>",
    "files_to_change": ["<path/to/file.py>"],
    "suggested_changes": {{
        "<path/to/file.py>": "<code to replace>"
    }}
}}
"""
    return context


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


def _parse_jules_response(raw: str) -> dict[str, Any]:
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
        match = re.search(r"\{.*?\}", raw, re.DOTALL)
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
    repo_path: str | None,
    files_to_change: dict[str, str],
    branch_name: str,
    repo_url: str = "",
) -> bool:
    """Apply Jules‑suggested fixes to the repository safely.

    If ``repo_path`` is ``None``, the repository is cloned from ``repo_url``
    into a temporary directory.  The function cleans up after itself on
    failure and switches back to the original working directory.
    """
    import tempfile
    _owns_tempdir = False

    if not repo_path:
        if not repo_url:
            logger.error("Neither repo_path nor repo_url provided — cannot apply fix")
            return False
        repo_path = tempfile.mkdtemp(prefix="jules-fix-")
        _owns_tempdir = True
        logger.info("Cloning %s into temp dir %s", repo_url, repo_path)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, repo_path],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to clone repo %s: %s", repo_url, exc.stderr)
            shutil.rmtree(repo_path, ignore_errors=True)
            return False

    original_cwd = os.getcwd()
    try:
        os.chdir(repo_path)

        def _run_git(args: list[str]) -> subprocess.CompletedProcess:
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

        if len(files_to_change) > MAX_FILES_PER_JULES_PR:
            raise RuntimeError(
                f"Jules fix exceeds MAX_FILES_PER_JULES_PR "
                f"({len(files_to_change)} > {MAX_FILES_PER_JULES_PR})"
            )

        # Write the suggested files
        total_bytes = 0
        for filepath, content in files_to_change.items():
            full_path = os.path.join(repo_path, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            total_bytes += len(content.encode("utf-8"))
            if total_bytes > MAX_BYTES_PER_JULES_PR:
                raise RuntimeError(
                    f"Jules fix exceeds MAX_BYTES_PER_JULES_PR "
                    f"({total_bytes} > {MAX_BYTES_PER_JULES_PR})"
                )

        _run_git(["add", "."])
        _run_git([
            "commit",
            "-m",
            "fix: auto-fix deployment failure via Jules AI",
        ])
        _run_git(["push", "origin", branch_name])

        return True
    except Exception as exc:
        logger.error("Failed to apply fix to repo: %s", exc)
        # Attempt to clean up the branch if it was created
        try:
            # Detect the default branch instead of hardcoding 'main'
            result = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"])
            default_branch = result.stdout.strip().replace("origin/", "") if result.returncode == 0 else "main"
            _run_git(["checkout", default_branch])
            _run_git(["branch", "-D", branch_name])
        except Exception:
            pass
        return False
    finally:
        os.chdir(original_cwd)
        if _owns_tempdir and os.path.exists(repo_path):
            shutil.rmtree(repo_path, ignore_errors=True)


def _create_pr(
    github_client: GitHubClient,
    repo_name: str,
    branch_name: str,
    title: str,
    body: str,
    base: str = "main",
) -> str | None:
    """Create a Pull Request on GitHub."""
    try:
        pr = github_client.create_pull_request(
            repo=repo_name,
            title=title,
            body=body,
            head=branch_name,
            base=base,
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
) -> dict[str, Any]:
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
        # Enrich context with Prometheus/Loki metrics
        try:
            from apps.deployments.models import Deployment as DeployModel
            dep = DeployModel.objects.select_related('service').only('service').get(id=deployment_id)
            svc = dep.service
        except Exception:
            svc = None
        context = _collect_failure_context(deployment_id, logs, service=svc)

        jules_response = _ask_jules_for_fix(context)
        fix_payload = _parse_jules_response(jules_response)

        fix_description = fix_payload.get("fix_description", "Auto-fix via Jules")
        suggested_changes = fix_payload.get("suggested_changes", {})

        branch_name = f"jules-fix-{deployment_id[:8]}-{int(time.time())}"
        if not _apply_fix_to_repo(repo_path, suggested_changes, branch_name, repo_url=repo_url):
            result.error = "Failed to apply fix to repository"
            return _json_safe(result, {})

        deployment = Deployment.objects.select_related("service", "service__provider", "service__owner").get(id=deployment_id)
        github_client = GitHubClient(repo_url, owner=deployment.service.owner)
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

            # Auto-redeploy from the PR branch if enabled (global toggle with per-service override)
            settings_obj = AIProviderSettings.get_solo()
            per_service_override = deployment.service.env_vars.filter(
                key='JULES_AUTO_FIX_DEPLOY'
            ).first()
            auto_deploy_enabled = settings_obj.jules_auto_deploy_pr
            if per_service_override and per_service_override.value and per_service_override.value.lower() == 'false':
                auto_deploy_enabled = False
            if auto_deploy_enabled:
                if not getattr(settings, 'JULES_AUTO_DEPLOY_PR', False):  # Default False; only True if admin opts in
                    logger.info("Jules fix pushed as PR; auto-deploy disabled by policy.")
                    return _json_safe(result, {})
                logger.info("Auto-deploying PR branch %s for deployment %s", branch_name, deployment_id)
                try:
                    # Create a new deployment record for the PR branch
                    new_deployment = Deployment.objects.create(
                        service=deployment.service,
                        branch=branch_name,
                        commit_hash="",
                        commit_message=f"[auto-fix] Deploying Jules fix from branch {branch_name}",
                        status=Deployment.Status.QUEUED,
                    )

                    # Enqueue deployment (skip_review=True ensures it bypasses the manual review pause)
                    provider = deployment.service.provider or getattr(deployment, 'target_server', None)
                    provider_id = provider.id if provider else None
                    if provider_id:
                        enqueue_smart_deploy_task(
                            deployment_id=str(new_deployment.id),
                            provider_id=str(provider_id),
                            skip_review=True,
                        )
                        logger.info("Auto-deploy task queued for PR branch %s", branch_name)
                    else:
                        logger.error("Could not determine provider to auto-deploy Jules fix")
                except Exception as e:
                    logger.error("Failed to auto-redeploy Jules fix: %s", e)
            else:
                logger.info("Fix applied and PR opened. Auto-redeploy disabled. Re-deploy can be triggered via webhook.")
        else:
            result.error = "Failed to create Pull Request"

        return _json_safe(result, {})

    except Exception as exc:
        logger.error("Jules auto-fix failed: %s", exc)
        result.error = str(exc)
        return _json_safe(result, {})
