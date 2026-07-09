# pylint: disable=line-too-long,logging-fstring-interpolation,subprocess-run-check,broad-exception-caught,too-many-nested-blocks,consider-using-with,too-few-public-methods,import-outside-toplevel
"""Builders module."""
# pylint: disable=no-member
"""Build manager service."""
import contextlib
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.utils import timezone
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# BuildKit corruption error signatures — if any of these appear in the
# error output, the build cache is corrupt and must be pruned before retry.
BUILDKIT_CACHE_ERROR_SIGNATURES = [
    'contenthash',
    'checksum.go',
    'lazyChecksum',
    'cacheContext',
    'cacheManager',
    # Registry cache import failures caused by auth/scope issues,
    # which corrupt the buildkit daemon's internal state.
    'registry cache importer',
    'insufficient_scope',
    'failed to configure registry cache',
]


def is_buildkit_cache_error(exc: Exception | str) -> bool:
    """Check if an exception is caused by BuildKit cache corruption."""
    msg = str(exc).lower()
    return any(sig.lower() in msg for sig in BUILDKIT_CACHE_ERROR_SIGNATURES)


def prune_buildkit_cache():
    """Prune Docker BuildKit cache to recover from corruption."""
    logger.warning("Pruning BuildKit cache after cache corruption error...")
    try:
        subprocess.run(
            ["docker", "builder", "prune", "-f"],
            capture_output=True, text=True, timeout=60
        )
        logger.info("BuildKit cache pruned successfully.")
    except Exception as e:
        logger.error("Failed to prune BuildKit cache: %s", e)


def cleanup_stuck_buildkit():
    """Remove stuck buildx BuildKit containers that block all Docker builds."""
    logger.warning("Checking for stuck BuildKit containers...")
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=buildx_buildkit",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30
        )
        for name in result.stdout.strip().splitlines():
            name = name.strip()
            if not name:
                continue
            logger.warning("Removing stuck BuildKit container: %s", name)
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, text=True, timeout=30
            )
    except Exception as e:
        logger.error("Failed to cleanup stuck BuildKit containers: %s", e)


def _before_retry(retry_state):
    """Called before each retry — prune cache if it was a BuildKit error."""
    exc = retry_state.outcome.exception()
    if exc and is_buildkit_cache_error(exc):
        prune_buildkit_cache()


class BuildManager:
    """
    Manages the build process (Code -> Docker Image).
    """

    def __init__(self, deployment):
        self.deployment = deployment
        self.service = deployment.service
        self.work_dir = Path(f"/tmp/builds/{self.deployment.id}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before_sleep=_before_retry,
    )
    def build_image(self):
        """
        Builds a Docker image from the repo with real-time log streaming.
        """
        registry = settings.CONTAINER_REGISTRY_URL
        image_tag = f"{registry}/{self.service.name}:{self.deployment.commit_hash[:7]}"
        logger.info(
            f"Starting build for {self.service.name} commit {self.deployment.commit_hash}")

        try:
            # 0. Login (if configured)
            if settings.REGISTRY_USER and settings.REGISTRY_PASSWORD:
                self._log(f"Logging in to {registry}...")
                # Pipe password via stdin for security
                login_proc = subprocess.Popen(
                    ["docker", "login", registry, "-u",
                        settings.REGISTRY_USER, "--password-stdin"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                _, stderr = login_proc.communicate(
                    input=settings.REGISTRY_PASSWORD)
                if login_proc.returncode != 0:
                    self._log(f"Login failed: {stderr}")
                    # Don't raise, try pushing anyway (might be using
                    # credential helper)
                else:
                    self._log("Login successful.")

            # 1. Clone
            repo_url = self.service.repository_url or ""
            self._log(f"Cloning repository {self._sanitize_repo_url(repo_url)}...")
            if self.work_dir.exists():
                shutil.rmtree(self.work_dir)
            self.work_dir.parent.mkdir(parents=True, exist_ok=True)

            github_token = self._get_github_access_token()
            if github_token and self._is_github_https(repo_url):
                try:
                    self._clone_github_repo_with_token(repo_url, self.service.branch, github_token)
                except subprocess.CalledProcessError:
                    # Token might be expired/revoked; retry public clone for public repos.
                    self._log("GitHub-auth clone failed; retrying without token...")
                    self._run_command(
                        ["git", "clone", "--branch", self.service.branch, repo_url, str(self.work_dir)]
                    )
            else:
                self._run_command(
                    ["git", "clone", "--branch", self.service.branch, repo_url, str(self.work_dir)]
                )

            # 2. Build
            dockerfile_path = self.work_dir / \
                self.service.root_directory.strip("/")
            self._log(f"Building Docker image from {dockerfile_path}...")

            if self.service.build_command:
                self._log(
                    f"NOTE: build_command '{self.service.build_command}' is ignored in Dockerfile mode. Only applicable for Buildpacks.")

            self._run_command(
                ["docker", "build", "-t", image_tag, "."],
                cwd=str(dockerfile_path)
            )

            # 3. Push
            self._log(f"Pushing image to {image_tag}...")
            # Note: Requires docker login to be handled in the base image or
            # host environment
            self._run_command(["docker", "push", image_tag])

            # 4. Security Scan (Simulated Trivy)
            self._run_security_scan(image_tag)

            self._log("Build and Push successful.")
            return image_tag

        except subprocess.CalledProcessError as e:
            self._log(f"Command failed with return code {e.returncode}")
            raise e
        except Exception as e:
            self._log(f"Build failed: {e!s}")
            raise e
        finally:
            # Cleanup
            if self.work_dir.exists():
                shutil.rmtree(self.work_dir)

    def _run_security_scan(self, image_tag):
        """
        Run Trivy vulnerability scan on the Docker image.
        Falls back to skip if Trivy is not installed.
        Reads settings from PlatformConfig (database) so the UI toggle
        actually controls the build pipeline.  Falls back to Django
        settings / env vars when PlatformConfig is unavailable.
        Raises BuildError when vulnerabilities at or above the threshold are found.
        """
        try:
            from apps.deployments.models_core import PlatformConfig
            config = PlatformConfig.load()
            trivy_enabled = bool(getattr(config, 'trivy_enabled', True))
            fail_on = str(getattr(config, 'trivy_fail_on_severity', 'CRITICAL') or 'CRITICAL').upper()
        except Exception:  # pylint: disable=broad-exception-caught
            trivy_enabled = getattr(settings, 'TRIVY_ENABLED', True)
            fail_on = getattr(settings, 'TRIVY_FAIL_ON_SEVERITY', 'CRITICAL').upper()

        if not trivy_enabled:
            self._log("Trivy scanning is disabled (trivy_enabled=false). Skipping.")
            return
        severities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
        if fail_on not in severities:
            logger.warning("Invalid TRIVY_FAIL_ON_SEVERITY=%r — falling back to CRITICAL", fail_on)
            fail_on = "CRITICAL"

        fail_idx = severities.index(fail_on)
        severity_arg = ",".join(severities[fail_idx:])

        try:
            from apps.deployments.utils import find_binary
            trivy_bin = find_binary("trivy")
            if not trivy_bin:
                self._log("WARNING: Trivy not installed — image built WITHOUT security scan. Install Trivy for vulnerability scanning.")
                return
            result = subprocess.run(
                [trivy_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                self._log("WARNING: Trivy not available — image built WITHOUT security scan.")
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, ImportError):
            self._log("WARNING: Trivy not installed — image built WITHOUT security scan. Install Trivy for vulnerability scanning.")
            return

        try:
            self._log(f"Running Trivy security scan (severity >= {fail_on})...")
            result = subprocess.run(
                [
                    trivy_bin, "image",
                    "--format", "json",
                    "--severity", severity_arg,
                    "--timeout", "5m",
                    image_tag
                ],
                capture_output=True,
                text=True,
                timeout=600
            )

            if result.returncode == 0 or result.stdout:
                import json
                try:
                    scan_results = json.loads(result.stdout)
                    vulns = {"critical": 0, "high": 0, "medium": 0, "low": 0}

                    for result_item in scan_results.get("Results", []):
                        for vuln in result_item.get("Vulnerabilities", []):
                            severity = vuln.get("Severity", "").lower()
                            if severity in vulns:
                                vulns[severity] += 1

                    self.deployment.vulnerability_report = {
                        "summary": vulns,
                        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "image": image_tag,
                        "fail_on": fail_on,
                        "passed": False,
                    }
                    self.deployment.save(
                        update_fields=['vulnerability_report'])

                    self._log(
                        f"Trivy scan: {vulns['critical']} critical, {vulns['high']} high, "
                        f"{vulns['medium']} medium, {vulns['low']} low")

                    blocking_severities = [s.lower() for s in severities[fail_idx:]]
                    fail_count = sum(vulns.get(s, 0) for s in blocking_severities)
                    if fail_count > 0:
                        self.deployment.vulnerability_report["passed"] = False
                        self.deployment.save(update_fields=['vulnerability_report'])
                        raise BuildError(
                            f"Security scan FAILED: {fail_count} vulnerabilities at or above "
                            f"{fail_on} severity. Image: {image_tag}. "
                            f"Fix the vulnerabilities or adjust TRIVY_FAIL_ON_SEVERITY in Platform Settings."
                        )

                    self.deployment.vulnerability_report["passed"] = True
                    self.deployment.save(update_fields=['vulnerability_report'])
                    self._log(f"Security scan passed (no {fail_on}+ issues found).")

                except json.JSONDecodeError:
                    self._log("Failed to parse Trivy output. Raw output saved.")
                    self.deployment.vulnerability_report = {
                        "error": "Parse failed", "raw": result.stdout[:1000]}
                    self.deployment.save(
                        update_fields=['vulnerability_report'])
            else:
                self._log(f"Trivy scan failed to execute: {result.stderr[:500]}")

        except BuildError:
            raise
        except subprocess.TimeoutExpired:
            self._log("Security scan timed out after 10 minutes.")
        except Exception as e:
            self._log(f"Security scan error: {e!s}")

    def _run_command(self, cmd, cwd=None, env=None):
        """Run command and stream output to logs."""
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            cwd=cwd,
            env=env,
            universal_newlines=True,
            bufsize=1  # Line buffered
        )

        for line in process.stdout:
            self._log(line.strip(), timestamp=False)

        process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)

    @staticmethod
    def _sanitize_repo_url(repo_url: str) -> str:
        """Remove userinfo from clone URLs to avoid leaking credentials in logs."""
        try:
            parsed = urlparse(repo_url)
            if not parsed.scheme or not parsed.netloc:
                return repo_url

            # Strip username/password while keeping host:port.
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            sanitized = parsed._replace(netloc=host)
            return urlunparse(sanitized)
        except Exception:
            return repo_url

    @staticmethod
    def _is_github_https(repo_url: str) -> bool:
        try:
            parsed = urlparse(repo_url)
            if parsed.scheme not in ("http", "https"):
                return False
            return (parsed.hostname or "").lower().endswith("github.com")
        except Exception:
            return False

    def _get_github_access_token(self) -> str | None:
        """
        Get the GitHub OAuth access token for the service owner (if linked).

        Checks token expiration and refreshes if needed. This relies on
        django-allauth storing social tokens.
        """
        user = getattr(self.service, "owner", None)
        if not user:
            return None

        try:
            from allauth.socialaccount.models import SocialAccount, SocialToken
        except Exception:
            return None

        account = (
            SocialAccount.objects.filter(user=user, provider="github")
            .order_by("-id")
            .first()
        )
        if not account:
            return None

        token_obj = (
            SocialToken.objects.filter(account=account)
            .order_by("-id")
            .first()
        )
        if not token_obj or not token_obj.token:
            return None

        # Check if token is expired and attempt refresh
        if token_obj.expires_at:
            from django.utils import timezone
            if token_obj.expires_at <= timezone.now():
                logger.info("GitHub token expired for user %s, attempting refresh", user)
                try:
                    from apps.deployments.views_github import _refresh_github_token
                    refreshed = _refresh_github_token(token_obj)
                    if not refreshed:
                        logger.warning(
                            "GitHub token refresh failed for user %s — "
                            "user may need to reconnect GitHub", user
                        )
                        return None
                    logger.info("GitHub token refreshed successfully for deployment")
                except Exception as exc:
                    logger.error("GitHub token refresh error: %s", exc)
                    return None

        return token_obj.token

    def _clone_github_repo_with_token(self, repo_url: str, branch: str, token: str) -> None:
        """
        Clone a GitHub repo over HTTPS using a linked OAuth token without putting
        the token in the clone URL or logs.
        """
        parsed = urlparse(repo_url)
        host = parsed.hostname or parsed.netloc
        if parsed.port:
            host = f"{host}:{parsed.port}"

        # Inject a username so git only asks for a password (the token).
        clone_url = urlunparse(parsed._replace(netloc=f"x-access-token@{host}"))

        askpass_path = self.work_dir.parent / f"askpass-{self.deployment.id}.sh"
        try:
            askpass_path.write_text('#!/bin/sh\nprintf \"%s\" \"$SMSLY_GIT_PASSWORD\" \n', encoding="utf-8")
            os.chmod(askpass_path, 0o700)

            env = os.environ.copy()
            env["GIT_ASKPASS"] = str(askpass_path)
            env["SMSLY_GIT_PASSWORD"] = token
            env["GIT_TERMINAL_PROMPT"] = "0"

            self._run_command(
                ["git", "clone", "--branch", branch, clone_url, str(self.work_dir)],
                env=env,
            )
        finally:
            with contextlib.suppress(Exception):
                askpass_path.unlink(missing_ok=True)

    def _log(self, message, timestamp=True):
        """Append logs to the deployment atomically and push to WebSocket."""
        from apps.deployments.models import Deployment
        from django.db.models import Value
        from django.db.models.functions import Concat

        prefix = f"[{time.strftime('%H:%M:%S')}] " if timestamp else ""
        log_line = f"{prefix}{message}\n"

        # Atomic append using Concat to avoid race condition
        Deployment.objects.filter(id=self.deployment.id).update(
            build_logs=Concat('build_logs', Value(log_line))
        )

        # Push to WebSocket channel layer for live streaming
        self._push_to_websocket(log_line)

    def _push_to_websocket(self, log_line):
        """Send a build log line to WebSocket consumers via channel layer."""
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return

            group_name = f"build_logs_{self.deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'build_log',
                    'log': log_line,
                    'status': self.deployment.status,
                    'timestamp': timezone.now().isoformat(),
                }
            )
        except Exception:
            # Never let WS errors break the build pipeline
            pass
