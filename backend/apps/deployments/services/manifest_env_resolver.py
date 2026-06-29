"""Manifest-backed environment variable resolver.
Reads actual repo files (.env.example, SECRETS-MANIFEST.yaml, stack markers)
to produce grounded env configurations — no AI hallucination."""

import logging
import os
import re
import secrets
import string
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def generate_strong_secret(length: int = 48) -> str:
    """Generate a cryptographically strong random secret."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Known addon URL patterns — vars whose values come from provisioned addons
ADDON_ENV_PATTERNS: dict[str, str] = {
    "DATABASE_URL": "POSTGRES_URL",
    "POSTGRES_URL": "POSTGRES_URL",
    "DB_URL": "POSTGRES_URL",
    "REDIS_URL": "REDIS_URL",
    "REDIS_URI": "REDIS_URL",
    "CELERY_BROKER_URL": "RABBITMQ_URL",
    "RABBITMQ_URL": "RABBITMQ_URL",
    "AMQP_URL": "RABBITMQ_URL",
    "MINIO_ENDPOINT": "MINIO_URL",
    "S3_ENDPOINT_URL": "MINIO_URL",
}

# Vars that are resolved at deploy-time by _build_runtime_env — skip setting
DEPLOY_TIME_VARS = {
    "PUBLIC_DOMAIN",
    "ALLOWED_HOSTS",
    "DJANGO_ALLOWED_HOSTS",
    "MARKETER_ALLOWED_HOSTS",
    "API_INTERNAL_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_NAME",
    "DB_PASSWORD",
    "SQL_HOST",
    "DATABASE",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "CELERY_RESULT_BACKEND",
    "CACHE_URL",
    "PORT",
    "HOSTNAME",
}

# Secret patterns — vars matching these get auto-generated values
SECRET_PATTERNS = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|DSN|CREDENTIAL|SIGNING_KEY|HASH_SALT|"
    r"ENCRYPTION_KEY|CACHE_ENCRYPTION_KEY|FIELD_ENCRYPTION_KEY)",
    re.IGNORECASE,
)

# Patterns that should NOT trigger secret generation (they're config, not secrets)
_SECRET_EXCLUSIONS = re.compile(
    r"(TTL|TIMEOUT|SECONDS|DAYS|HOURS|MINUTES|LIMIT|PORT|COUNT|COOLDOWN|"
    r"INTERVAL|RETRIES|CACHE_TTL|ROTATION_|THRESHOLD|WEIGHT|DECAY_|"
    r"SIGNAL_|ANOMALY_|RISK_SCORE|COLLECT_|API_KEY_CACHE|SECRET_ROTATION|"
    r"KEY_ROTATION|NONCE_TTL|SDK_DEMO|SDK_INSTALL)",
    re.IGNORECASE,
)

# Service URL patterns — vars that reference another service's URL
_SERVICE_URL_PATTERNS = re.compile(
    r"(URL|ENDPOINT|HOST|BASE_URL|API_URL|GATEWAY_URL|SERVICE_URL|HEALTH_URL)$",
    re.IGNORECASE,
)


class ManifestEnvResolver:
    """Resolves environment variables by reading actual repo files.

    Resolution priority:
      1. Cross-service secret (from SECRETS-MANIFEST.yaml expects_from)
      2. Known addon pattern (DATABASE_URL -> POSTGRES_URL placeholder)
      3. Secret pattern -> generate_strong_secret()
      4. Non-empty default from .env.example
      5. Deploy-time var -> skip (will be set at runtime)
      6. Otherwise -> flag as unresolved (returns empty string)
    """

    def __init__(
        self,
        source_dir: str | None = None,
        service_name: str = "",
        cross_service_map: dict[str, Any] | None = None,
    ):
        self.source_dir = source_dir
        self.service_name = service_name
        self.cross_service_map = cross_service_map or {}

        # Populated during resolution
        self.is_frontend = False
        self.stack = "python"
        self.port = 8000
        self.env_example_vars: dict[str, str] = {}
        self.secrets_manifest: dict[str, Any] = {"serves_as": [], "expects_from": []}
        self.unresolved_vars: list[str] = []
        self.resolved_env: dict[str, str] = {}

    def resolve_all(self) -> dict[str, str]:
        """Full resolution pipeline. Returns populated env dict."""
        if not self.source_dir or not os.path.isdir(self.source_dir):
            logger.warning(
                "No source_dir provided for %s; returning empty env",
                self.service_name,
            )
            return {}

        self._scan_env_example()
        self._scan_secrets_manifest()
        self._detect_stack()
        self._detect_port()
        self._detect_frontend()

        resolved: dict[str, str] = {}

        for var_name, default_val in self.env_example_vars.items():
            value = self._resolve_var(var_name, default_val)
            if value is not None:
                resolved[var_name] = value

        # Add stack defaults if not already in .env.example
        if self.is_frontend:
            resolved.setdefault("NODE_ENV", "production")
            resolved.setdefault("PORT", str(self.port))
        else:
            resolved.setdefault("PYTHONUNBUFFERED", "1")
            resolved.setdefault("PORT", str(self.port))

        self.resolved_env = resolved
        return resolved

    # ------------------------------------------------------------------
    # Scanning helpers
    # ------------------------------------------------------------------

    def _scan_env_example(self) -> None:
        """Parse .env.example from root or common subdirs."""
        candidates = [
            os.path.join(self.source_dir, ".env.example"),
            os.path.join(self.source_dir, ".env.production"),
            os.path.join(self.source_dir, ".env"),
        ]
        # Also check common framework subdirs
        for sub in ("backend", "app", "server", "src", "api"):
            candidates.append(os.path.join(self.source_dir, sub, ".env.example"))
            candidates.append(os.path.join(self.source_dir, sub, ".env.production"))

        seen = set()
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#"):
                            continue
                        # Strip 'export '
                        line = re.sub(r"^export\s+", "", line)
                        if "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        key = key.strip().upper()
                        val = val.strip().strip("\"'")
                        if key and key not in seen:
                            seen.add(key)
                            # Treat placeholder/default comments as empty
                            if val and val.lower() not in (
                                "changeme",
                                "change_me",
                                "your-value-here",
                                "your_secret_key",
                                "",
                            ):
                                self.env_example_vars[key] = val
                            else:
                                self.env_example_vars[key] = ""
            except OSError:
                continue

        logger.debug(
            "Found %d vars in .env.example for %s",
            len(self.env_example_vars),
            self.service_name,
        )

    def _scan_secrets_manifest(self) -> None:
        """Parse SECRETS-MANIFEST.yaml if present."""
        import yaml

        candidates = [
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yaml"),
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yml"),
        ]
        for sub in ("backend", "app", "server"):
            candidates.append(
                os.path.join(self.source_dir, sub, "SECRETS-MANIFEST.yaml")
            )

        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    self.secrets_manifest = data
                    logger.debug(
                        "Loaded SECRETS-MANIFEST from %s", path
                    )
                return
            except Exception as e:
                logger.warning(
                    "Failed to parse SECRETS-MANIFEST at %s: %s", path, e
                )

    def _detect_stack(self) -> None:
        """Detect stack from actual files — NO AI guessing."""
        srcdir = self.source_dir or ""

        # Django detection (manage.py is definitive)
        if self._find_file(srcdir, "manage.py") and self._find_file(
            srcdir, "requirements.txt"
        ):
            self.stack = "django"
            return

        # Next.js detection
        if self._find_glob(srcdir, "next.config.*") and self._find_file(
            srcdir, "package.json"
        ):
            self.stack = "nextjs"
            return

        # Generic Python
        if self._find_file(srcdir, "requirements.txt") or self._find_file(
            srcdir, "pyproject.toml"
        ):
            self.stack = "python"
            return

        # Node.js
        if self._find_file(srcdir, "package.json"):
            # Check if it's a frontend framework
            self.stack = "node"
            try:
                import json

                pkg_path = self._find_file_path(srcdir, "package.json")
                if pkg_path:
                    with open(pkg_path, encoding="utf-8") as f:
                        pkg = json.load(f)
                    deps = {
                        **(pkg.get("dependencies", {})),
                        **(pkg.get("devDependencies", {})),
                    }
                    if "next" in deps:
                        self.stack = "nextjs"
                    elif "react" in deps or "vue" in deps or "svelte" in deps:
                        self.stack = "node"  # frontend framework
            except Exception:
                pass
            return

        # Rust detection
        if self._find_file(srcdir, "Cargo.toml"):
            self.stack = "rust"
            return

        logger.debug("Could not detect stack for %s; defaulting to python", self.service_name)

    def _detect_port(self) -> None:
        """Detect port from Dockerfile EXPOSE directive."""
        srcdir = self.source_dir or ""
        dockerfiles = self._find_files(srcdir, "Dockerfile*")
        for df in dockerfiles:
            try:
                with open(df, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        match = re.match(r"EXPOSE\s+(\d+)", line.strip(), re.IGNORECASE)
                        if match:
                            self.port = int(match.group(1))
                            return
            except OSError:
                continue

        # Fall back to .env.example PORT default
        if "PORT" in self.env_example_vars and self.env_example_vars["PORT"]:
            try:
                self.port = int(self.env_example_vars["PORT"])
            except (ValueError, TypeError):
                pass

        # Stack-based fallback
        if self.is_frontend or self.stack in ("nextjs", "node"):
            self.port = 3000
        else:
            self.port = 8000

    def _detect_frontend(self) -> None:
        """Detect if this is a frontend-only service."""
        # Explicit frontend names
        frontend_names = {"frontend", "backoffice-web", "web", "ui", "dashboard"}
        name_lower = self.service_name.lower().replace("-", "").replace("_", "")
        if any(fn in name_lower for fn in ("frontend", "backoffice", "webui", "dashboard")):
            self.is_frontend = True
            return

        # Stack-based detection
        if self.stack == "nextjs":
            # Is it Next.js with a backend counterpart?
            # If source has NO manage.py / requirements.txt, it's frontend-only
            srcdir = self.source_dir or ""
            has_backend = bool(
                self._find_file(srcdir, "manage.py")
                or self._find_file(srcdir, "requirements.txt")
                and not self._find_file(srcdir, "package.json")
            )
            self.is_frontend = not has_backend
            return

    # ------------------------------------------------------------------
    # Variable resolution
    # ------------------------------------------------------------------

    def _resolve_var(self, var_name: str, default_val: str) -> str | None:
        """Resolve a single env var. Returns value, or None to skip, or empty string if unresolved."""
        # Deploy-time vars — skip (resolved at runtime)
        if var_name in DEPLOY_TIME_VARS:
            return None

        # Cross-service secret from SECRETS-MANIFEST
        secret_value = self._resolve_cross_service_secret(var_name)
        if secret_value:
            return secret_value

        # Known addon pattern
        if var_name in ADDON_ENV_PATTERNS:
            placeholder = ADDON_ENV_PATTERNS[var_name]
            return f"{{{{{placeholder}}}}}"

        # Service URL pattern — check if it references another service
        service_url = self._resolve_service_url(var_name)
        if service_url:
            return service_url

        # Secret pattern — auto-generate
        if SECRET_PATTERNS.search(var_name) and not _SECRET_EXCLUSIONS.search(var_name):
            return generate_strong_secret(48)

        # Non-empty default from .env.example
        if default_val:
            return default_val

        # Empty required var — flag as unresolved
        self.unresolved_vars.append(var_name)
        return ""

    def _resolve_cross_service_secret(self, var_name: str) -> str | None:
        """Check if var_name is listed in this service's expects_from in SECRETS-MANIFEST."""
        for entry in self.secrets_manifest.get("expects_from", []):
            if isinstance(entry, dict):
                for local_var, mapping in entry.items():
                    if local_var == var_name:
                        # Look up in cross_service_map for the paired value
                        if self.cross_service_map:
                            return self._lookup_paired_secret(
                                var_name, mapping
                            )
                        # No map available — generate new secret
                        return generate_strong_secret(48)
            elif isinstance(entry, str) and "→" in entry:
                # Handle "KEY → service (VAR)" format
                parts = entry.split("→")
                local_part = parts[0].strip()
                if local_part == var_name:
                    return generate_strong_secret(48)
        return None

    def _lookup_paired_secret(self, local_var: str, mapping: str) -> str | None:
        """Look up a previously-generated paired secret from the cross-service map."""
        # mapping is like "smsly-security-gateway (GATEWAY_TO_PLATFORM_SECRET)"
        import re as _re

        match = _re.search(r"\(([^)]+)\)", mapping)
        if match:
            remote_var = match.group(1)
            # The map stores secrets keyed by (service, var)
            # Check if this pair was already resolved
            for svc_name, svc_data in self.cross_service_map.get("resolved", {}).items():
                if remote_var in svc_data:
                    return svc_data[remote_var]
        return generate_strong_secret(48)

    def _resolve_service_url(self, var_name: str) -> str | None:
        """Resolve vars like POLICY_SERVICE_URL -> {{SERVICE:smsly-policy-service}}."""
        if not _SERVICE_URL_PATTERNS.search(var_name):
            return None

        # Map common names to actual service names
        service_name_map = {
            "PLATFORM_API": "smsly-platform-api",
            "IDENTITY_SERVICE": "smsly-identity-service",
            "POLICY_SERVICE": "smsly-policy-service",
            "AUDIT_SERVICE": "smsly-audit-log-service",
            "SECURITY_GATEWAY": "smsly-security-gateway",
            "RATE_LIMIT_SERVICE": "smsly-rate-limit-service",
            "BACKEND": "smsly-backend",
            "TRANSACTION_CHAIN": "smsly-transaction-chain",
            "GATEWAY": "smsly-security-gateway",
            "FRONTEND": "smsly-frontend",
        }

        # Strip common suffixes to find the service key
        stem = re.sub(
            r"_(URL|ENDPOINT|HOST|BASE_URL|API_URL|GATEWAY_URL|SERVICE_URL|HEALTH_URL)$",
            "",
            var_name,
        )
        # Also strip prefixes like NEXT_PUBLIC_
        stem = re.sub(r"^(NEXT_PUBLIC_|VITE_|REACT_APP_)", "", stem)

        if stem in service_name_map:
            return f"{{{{SERVICE:{service_name_map[stem]}}}}}"

        # Try partial match
        for key, svc in service_name_map.items():
            key_stem = key.replace("_", "").lower()
            stem_clean = stem.replace("_", "").lower()
            if key_stem in stem_clean or stem_clean in key_stem:
                return f"{{{{SERVICE:{svc}}}}}"

        return None

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_file(base_dir: str, filename: str) -> bool:
        """Check if filename exists in base_dir or one level deep."""
        if os.path.isfile(os.path.join(base_dir, filename)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if os.path.isfile(os.path.join(subpath, filename)):
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def _find_file_path(base_dir: str, filename: str) -> str | None:
        """Return full path of filename in base_dir or one level deep."""
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            return path
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    candidate = os.path.join(subpath, filename)
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass
        return None

    @staticmethod
    def _find_files(base_dir: str, pattern: str) -> list[str]:
        """Find files matching glob pattern in base_dir or one level deep."""
        import glob as _glob

        results = _glob.glob(os.path.join(base_dir, pattern))
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    results.extend(_glob.glob(os.path.join(subpath, pattern)))
        except OSError:
            pass
        return results

    @staticmethod
    def _find_glob(base_dir: str, pattern: str) -> bool:
        """Check if any file matches glob pattern."""
        import glob as _glob

        if _glob.glob(os.path.join(base_dir, pattern)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if _glob.glob(os.path.join(subpath, pattern)):
                        return True
        except OSError:
            pass
        return False
