import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse as parse_url

import git
from django.utils import timezone

from apps.deployments.models import EnvironmentVariable
from apps.deployments.utils import append_log, log_exhaustive_clone_diagnostics, redact_values, update_stage
from .exceptions import BuildError


logger = logging.getLogger(__name__)


class CloneMixin:
    def _clone_repo(self):
        """Step 1: Clone Repository."""
        update_stage(self.deployment, 'Clone', 'running')
        start_time = timezone.now()
        requested_branch = (self.service.branch or 'main').strip() or 'main'

        try:
            append_log(
                self.deployment,
                f"Cloning {self.service.repository_url} (branch: {requested_branch})...\n"
            )

            repo_token = None
            try:
                parsed = parse_url(self.service.repository_url or "")
                if (parsed.scheme in ("http", "https") and
                        (parsed.hostname or "").lower().endswith("github.com")):
                    service_owner = getattr(self.service, "owner", None)
                    # Use the priority chain: GitHub App token > user OAuth token > None.
                    # Falls back gracefully when App is not configured.
                    repo_full_name = "/".join(
                        (parsed.path or "").lstrip("/").rstrip(".git").split("/")[:2]
                    )
                    from apps.deployments.utils import get_github_token_for_repo
                    repo_token = get_github_token_for_repo(service_owner, repo_full_name)
                    if repo_token:
                        append_log(
                            self.deployment,
                            "GitHub credentials resolved for private repo access...\n"
                        )
                    else:
                        logger.warning(
                            "No GitHub token found for service owner %s (service: %s). "
                            "Configure a GitHub App or connect a GitHub account.",
                            service_owner.id if service_owner else "None",
                            self.service.id
                        )
                        append_log(
                            self.deployment,
                            "⚠ No GitHub credentials available. If this is a private repo, "
                            "connect your GitHub account in Settings or configure a GitHub App.\n"
                        )
            except Exception as exc:
                logger.warning("Error retrieving GitHub token: %s", exc)

            target_commit = getattr(self.deployment, 'commit_hash', None)
            if target_commit and target_commit.upper() in ('HEAD', 'LATEST', 'TEMPLATE', 'ECOSYSTEM-DEPLOY'):
                target_commit = None

            self._clone_with_github_token(
                self.service.repository_url,
                requested_branch,
                repo_token,
                self.build_dir,
                target_commit=target_commit,
            )
            self.source_dir = self.build_dir

            # Metadata
            # pylint: disable=no-member
            repo = git.Repo(self.source_dir)
            self.deployment.commit_hash = repo.head.commit.hexsha
            self.deployment.commit_message = repo.head.commit.message
            self.deployment.save(update_fields=['commit_hash', 'commit_message'])

            update_stage(
                self.deployment, 'Clone', 'success',
                (timezone.now() - start_time).total_seconds()
            )
            append_log(
                self.deployment,
                f"✓ Cloned successfully. Commit: {self.deployment.commit_hash[:7]}\n"
            )
            log_exhaustive_clone_diagnostics(self.deployment, self.service.repository_url, requested_branch, self.source_dir)

        except Exception as e:
            update_stage(self.deployment, 'Clone', 'failed')
            raise BuildError(f"Clone failed: {e!s}") from e

        # Auto-inject .env file from repo (if present)
        self._inject_dotenv_from_repo()



    def _clone_with_github_token(self, repo_url: str, branch: str, token: str | None, target_dir: str, target_commit: str | None = None):
        """Clone repository into *target_dir* using an atomic clone-then-rename strategy.

        Clones into a uniquely-named temporary sibling directory first, then
        renames it into *target_dir*.  This prevents a concurrent deployment for
        the same service from racing into a half-written clone directory and
        hitting ``fatal: destination path already exists``.
        """
        build_path = Path(target_dir)
        parent = build_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            logger.warning("Could not mkdir in %s (%s), falling back to temp dir", parent, exc)
            parent = Path(tempfile.gettempdir()) / 'smsly-builds'
            parent.mkdir(parents=True, exist_ok=True)
            build_path = parent / build_path.name
            self.build_dir = str(build_path)
            if hasattr(self, 'source_dir') and self.source_dir:
                self.source_dir = str(build_path)

        # Clone into a unique temp dir inside the same parent, then atomically
        # rename into the final location.  This means:
        #   * No other process ever sees a half-written clone.
        #   * If the clone fails, the stale temp dir is cleaned up, not target_dir.
        tmp_path = Path(tempfile.mkdtemp(dir=str(parent), prefix=f".clone_tmp_{build_path.name}_"))

        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        askpass_path = None

        if token:
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(repo_url)
            host = parsed.hostname or "github.com"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            remote_url = urlunparse(parsed._replace(netloc=f"x-access-token@{host}"))

            askpass_fd, askpass_name = tempfile.mkstemp(
                prefix=".smsly-git-askpass-",
                suffix=".sh",
                dir=str(build_path.parent),
            )
            os.close(askpass_fd)
            askpass_path = Path(askpass_name)
            askpass_path.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  *Username*) printf \"%s\" \"x-access-token\" ;;\n"
                "  *) printf \"%s\" \"$SMSLY_GIT_PASSWORD\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            os.chmod(askpass_path, 0o700)
            env["GIT_ASKPASS"] = str(askpass_path)
            env["SMSLY_GIT_PASSWORD"] = token
        else:
            remote_url = repo_url

        clone_cmd = [
            "git", "clone", "--branch", branch, "--single-branch", remote_url, str(tmp_path)
        ]
        try:
            subprocess.run(
                clone_cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if target_commit:
                checkout_cmd = ["git", "checkout", target_commit]
                subprocess.run(
                    checkout_cmd,
                    cwd=str(tmp_path),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
            # Atomically promote tmp_path → build_path
            if build_path.exists():
                shutil.rmtree(build_path, ignore_errors=True)
            tmp_path.rename(build_path)
            tmp_path = None  # ownership transferred
        except subprocess.CalledProcessError as exc:
            details = self._format_git_clone_error(exc, token)
            raise RuntimeError(details) from exc
        finally:
            if askpass_path and askpass_path.exists():
                try:
                    askpass_path.unlink()
                except OSError:
                    pass
            if tmp_path is not None and tmp_path.exists():
                shutil.rmtree(tmp_path, ignore_errors=True)



    def _format_git_clone_error(self, exc: subprocess.CalledProcessError, token: str | None) -> str:
        """Return a concise, redacted clone failure with Git's real stderr."""
        parts = [f"git clone exited with code {exc.returncode}"]
        stream_values = (
            ("stderr", exc.stderr),
            ("stdout", exc.stdout or exc.output),
        )
        for label, value in stream_values:
            if not value:
                continue
            text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
            text = text.strip()
            if not text:
                continue
            redaction_values = [token] if token else []
            text = redact_values(text, redaction_values + getattr(self, "secret_values", []))
            parts.append(f"{label}: {text}")
        return " | ".join(parts)



    def _inject_dotenv_from_repo(self):
        """Auto-inject env vars from .env files found in the cloned repo.

        Scans root + common framework subdirs for .env files.
        Priority: .env.production > .env.local > .env
        Only injects keys not already set. Never injects empty values.
        """
        if not self.source_dir:
            return

        # Common framework subdirectories to scan
        SCAN_DIRS = [
            '',  # repo root
            'frontend', 'backend', 'server', 'app', 'src',
            'api', 'web', 'client', 'services',
        ]
        # .env file names in priority order (later overrides earlier)
        ENV_FILES = ['.env', '.env.local', '.env.production']

        # Keys we should NEVER inject from .env files (security)
        SKIP_PATTERNS = re.compile(
            r'(SECRET|PRIVATE|TOKEN|PASSWORD|API[_-]?KEY|DSN|CREDENTIAL)',
            re.IGNORECASE,
        )

        collected = {}  # key -> value (later files override)

        for subdir in SCAN_DIRS:
            scan_path = os.path.join(self.source_dir, subdir) if subdir else self.source_dir
            if not os.path.isdir(scan_path):
                continue

            for env_file in ENV_FILES:
                env_path = os.path.join(scan_path, env_file)
                if not os.path.isfile(env_path):
                    continue

                try:
                    with open(env_path, encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            line = line.strip()
                            # Skip empty lines, comments, exports
                            if not line or line.startswith('#'):
                                continue
                            line = re.sub(r'^export\s+', '', line)

                            if '=' not in line:
                                continue

                            key, _, value = line.partition('=')
                            key = key.strip().upper()
                            value = value.strip()

                            # Strip surrounding quotes
                            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                                value = value[1:-1]

                            if not key or not value:
                                continue
                            if SKIP_PATTERNS.search(key):
                                continue

                            # Sanitize for PostgreSQL
                            safe_key = key.replace('\x00', '')
                            safe_value = value.replace('\x00', '')

                            collected[safe_key] = safe_value
                except Exception:
                    continue

        if not collected:
            return

        # Inject into DB (only keys not already set)
        injected = 0
        for key, value in collected.items():
            is_secret = bool(re.search(
                r'(TOKEN|API_KEY|SECRET|PRIVATE)',
                key, re.IGNORECASE,
            ))
            _, created = EnvironmentVariable.objects.get_or_create(
                service=self.service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret},
            )
            if created:
                display_val = '********' if is_secret else value[:50]
                append_log(
                    self.deployment,
                    f"  📄 .env: {key}={display_val}\n"
                )
                injected += 1

        if injected:
            append_log(
                self.deployment,
                f"\n✅ Auto-injected {injected} env var(s) from repo .env files.\n"
            )

