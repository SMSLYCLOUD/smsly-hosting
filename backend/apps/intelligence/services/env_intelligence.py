import json
import logging
import re
import secrets
from typing import Any

from apps.intelligence.providers import _cached_ask

logger = logging.getLogger(__name__)

# Vars managed by platform — AI Senate must NOT fill these.
# Includes addon-provisioned vars (DB, Redis, S3) and domain vars
# (ALLOWED_HOSTS, PUBLIC_DOMAIN) that are resolved at deploy time.
_PLATFORM_MANAGED_VARS = frozenset({
    "DATABASE_URL", "POSTGRES_URL", "DB_URL", "DB_URI",
    "SQLALCHEMY_DATABASE_URI", "SQLALCHEMY_DATABASE_URL",
    "REDIS_URL", "REDIS_URI",
    "CELERY_BROKER_URL", "BROKER_URL", "AMQP_URL",
    "CELERY_RESULT_BACKEND", "RESULT_BACKEND",
    "MONGODB_URI", "MONGO_URI", "MONGO_URL",
    "MINIO_ENDPOINT", "S3_ENDPOINT_URL",
    "DB_HOST", "DB_PORT", "DB_USER", "DB_NAME", "DB_PASSWORD",
    "SQL_HOST", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
    "POSTGRES_DB", "POSTGRES_PASSWORD",
    # Domain-aware — resolved at deploy time from service.public_domain
    "PUBLIC_DOMAIN", "ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS",
    "MARKETER_ALLOWED_HOSTS", "API_INTERNAL_URL",
    # Platform URLs — resolved by ecosystem linker at deploy time
    "SMSLY_BACKEND_URL", "BACKEND_URL",
    "IDENTITY_SERVICE_URL", "PLATFORM_API_URL",
})

_DELIBERATION_RE = re.compile(
    r'^(?:'
    r'boolean\s*=>\s*(true|false)\b.*'   # "boolean => likely true (or false?)..."
    r'|number\s*(?:\([^)]*\))?\s*=>\s*([0-9.e+-]+)\b.*'  # "number (seconds) => 5"
    r'|URL\s*=>\s*(\S+).*'              # "URL => http://..."
    r'|string\s*=>\s*(.+)'              # "string => /path/..."
    r')$',
    re.IGNORECASE,
)


def _sanitize_senate_value(val: str) -> str:
    """Strip AI deliberation text from a value (e.g. 'boolean => likely true ...' → 'true')."""
    v = str(val).strip()
    m = _DELIBERATION_RE.match(v)
    if m:
        # Return the first non-None group (the actual value)
        for g in m.groups():
            if g is not None:
                return g.strip().strip('"').strip("'")
    return v


class EnvironmentIntelligenceService:
    """
    Service that uses the AI Senate to intelligently fill environment variables
    based on code context and platform standards.
    """

    SYSTEM_PROMPT = (
        "You are the SMSLY AI Senate Committee. Your mission is to suggest production-safe "
        "values for the environment variables listed in the request only.\n"
        "RULES:\n"
        "1. NEVER invent new variables — only fill the ones listed below. Skip anything you cannot determine.\n"
        "2. SECRETS: Use 'GENERATE' for keys/tokens/passwords. We will generate high-entropy hex strings.\n"
        "3. LINKING: Use internal service names (e.g., http://service-name:PORT) for cross-service URLs.\n"
        "4. NEVER set PORT — the platform manages it.\n"
        "5. STACK AWARENESS: Only set vars appropriate for the detected stack.\n"
        "6. USE PROPER JSON TYPES:\n"
        "   - Numbers: write 8000 not \"8000\"\n"
        "   - Booleans: write true/false not \"true\"/\"false\"\n"
        "   - Strings: only use quotes for actual string values\n"
        "7. If you cannot determine a proper value, omit the variable entirely (return null for that key).\n"
        "Return valid JSON only, containing ONLY the variables listed in the request."
    )

    @classmethod
    def resolve_environment(cls, env_context: dict[str, str], stack: str = "", service_name: str = "", fill_keys: set[str] | None = None) -> dict[str, str]:
        """
        Takes the full env dict and returns suggested values for vars in fill_keys.

        Args:
            env_context: ALL env vars for the service (key → value). Used as context.
            stack: Detected tech stack (e.g. "python", "node").
            service_name: Name of the service.
            fill_keys: Only fill these vars. If None, fill all vars.
        """
        if not env_context:
            return {}

        if fill_keys is None:
            fill_keys = set(env_context.keys())

        # Prepare the committee brief — show ALL vars for context
        brief_lines = [
            f"Service Name: {service_name}",
            f"Detected Stack: {stack}\n",
            "All environment variables (existing values shown for context):"
        ]
        for var, val in sorted(env_context.items()):
            val_str = str(val or "").strip()
            if var in fill_keys:
                brief_lines.append(f"- {var}: [NEEDS VALUE] (current: {val_str or '(empty)'})")
            else:
                brief_lines.append(f"- {var}: {val_str or '(empty)'}")

        prompt = (
            "Review every environment variable below.\n"
            f"Only provide values for variables marked [NEEDS VALUE] ({len(fill_keys)} variables).\n"
            "Use the existing values as context to make better decisions.\n"
            "For SECRETS: state 'GENERATE'.\n"
            "For INTERNAL URLs: use service names (e.g., http://service-name:PORT).\n"
            "NEVER set PORT — the platform manages it.\n"
            f"STACK: {stack}. Only set vars appropriate for this stack.\n"
            "For standard settings: use production-safe defaults.\n\n"
            "Return a JSON object with ONLY the [NEEDS VALUE] variables: { \"VAR_NAME\": \"value\" }\n\n"
            + "\n".join(brief_lines)
        )

        try:
            response_text, provider = _cached_ask(prompt, cls.SYSTEM_PROMPT)
            logger.info("Senate resolution for %s delivered by %s", service_name, provider)

            # Extract JSON from response — use non-greedy match to avoid capturing
            # Markdown or deliberation text between multiple JSON blocks.
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response_text, re.DOTALL)
            if json_match:
                try:
                    suggestions = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    suggestions = {}
            else:
                suggestions = {}

            if not suggestions:
                # Fallback: line-by-line parsing with stricter key validation
                for line in response_text.splitlines():
                    line = line.strip()
                    if ':' not in line:
                        continue
                    parts = line.split(':', 1)
                    k = parts[0].strip().strip('"').strip("'").strip("- ").strip("*_#")
                    v = parts[1].strip().strip('"').strip("'").strip(",")
                    # Validate: keys must start with a letter, underscore, or number and
                    # contain only valid env var characters. Reject Markdown/rich text.
                    if k and v and re.match(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$', k):
                        suggestions[k] = v

            # Post-process suggestions
            # Config/integer vars that should NEVER get token_hex even if they
            # match secret keyword patterns (e.g. AI_MAX_TOKENS, SD_x_TTL_DAYS).
            _CONFIG_PATTERNS = {
                "TTL", "TIMEOUT", "SECONDS", "DAYS", "HOURS", "MINUTES",
                "MAX_", "MIN_", "LIMIT", "COUNT", "COOLDOWN",
                "CACHE_TTL", "ROTATION_", "INTERVAL", "RETRIES", "SIZE",
            }
            final_env = {}
            for var, val in suggestions.items():
                val = _sanitize_senate_value(val)
                var_upper = var.upper()
                # Skip PORT entirely — platform manages it
                if var_upper == "PORT" or var_upper.endswith("_PORT"):
                    continue
                # Skip platform-managed vars (addon provisioning handles these)
                if var_upper in _PLATFORM_MANAGED_VARS:
                    continue
                # Config vars keep AI values
                if any(p in var_upper for p in _CONFIG_PATTERNS):
                    final_env[var] = str(val)
                    continue
                # Only generate secrets when AI explicitly says "GENERATE".
                # If AI returned a real value, trust it even if the var name
                # matches secret patterns — the AI knew what it was doing.
                if val == "GENERATE":
                    from apps.cloud.services.build_constants import is_secret_env_var
                    if is_secret_env_var(var_upper):
                        if "ENCRYPTION_KEY" in var_upper:
                            import base64
                            final_env[var] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
                        else:
                            final_env[var] = secrets.token_hex(32)
                    else:
                        # AI's GENERATE on a non-secret var — don't fill
                        continue
                    continue

                # AI provided a value — trust it.
                if "URL" in var_upper or "HOST" in var_upper:
                    # Sanitize URL suggestions to be internal-first
                    if "localhost" in str(val) or "127.0.0.1" in str(val):
                        final_env[var] = str(val).replace("localhost", service_name).replace("127.0.0.1", service_name)
                    else:
                        final_env[var] = str(val)
                else:
                    final_env[var] = str(val)

            return final_env

        except Exception as e:
            logger.error("AI Senate failed to resolve environment for %s: %s", service_name, e)
            fallback = {}
            import base64
            for var in env_context:
                var_upper = var.upper()
                if var_upper in _PLATFORM_MANAGED_VARS:
                    continue
                if var_upper == "PORT" or var_upper.endswith("_PORT"):
                    continue
                # Only generate secrets for clearly secret-named vars.
                # Everything else is left empty — the user must fill it.
                from apps.cloud.services.build_constants import is_secret_env_var
                if is_secret_env_var(var_upper):
                    if "ENCRYPTION_KEY" in var_upper:
                        fallback[var] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
                    else:
                        fallback[var] = secrets.token_hex(32)
            return fallback

    @classmethod
    def resolve_ecosystem(cls, services_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Resolves environment variables for a whole cluster of services at once.
        Ensures cross-service URL consistency.
        """
        if not services_data:
            return []

        committee_brief = ["## Ecosystem Deployment Analysis Requested\n"]
        for svc in services_data:
            name = svc.get('name', 'unknown')
            stack = svc.get('stack', 'unknown')
            vars_ctx = svc.get('env_vars_context', {})

            committee_brief.append(f"### Service: {name} (Stack: {stack})")
            for var, ctxs in vars_ctx.items():
                committee_brief.append(f"- {var}: {' | '.join(ctxs[:1])}")
            committee_brief.append("")

        prompt = (
            "You are resolving environment variables for an interconnected ecosystem of microservices.\n"
            "GOAL: Produce a perfectly linked cluster where all services know how to talk to each other.\n\n"
            "LINKING RULES:\n"
            "1. If Service A depends on Service B, map Service A's URL variables (API_URL, etc.) to 'http://service-b:PORT'.\n"
            "2. Map database/cache variables to {{POSTGRES_URL}}, {{REDIS_URL}}, etc.\n"
            "3. Every single variable in the brief MUST be present in your JSON output.\n\n"
            "Return a JSON object where keys are SERVICE NAMES and values are objects of their env vars.\n\n"
            + "\n".join(committee_brief)
        )

        try:
            response_text, provider = _cached_ask(prompt, cls.SYSTEM_PROMPT)
            logger.info("Ecosystem Senate resolution delivered by %s", provider)

            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                return services_data

            all_suggestions = json.loads(json_match.group(0))

            for svc in services_data:
                name = svc.get('name')
                if name in all_suggestions:
                    suggestions = all_suggestions[name]
                    _CONFIG_ECO = {"TTL", "TIMEOUT", "SECONDS", "DAYS", "HOURS", "MINUTES", "MAX_", "MIN_", "LIMIT", "COUNT", "COOLDOWN", "CACHE_TTL", "ROTATION_", "INTERVAL", "RETRIES"}
                    final_env = {}
                    for var, val in suggestions.items():
                        val = _sanitize_senate_value(val)
                        var_u = var.upper()
                        if var_u == "PORT" or var_u.endswith("_PORT"):
                            continue
                        if var_u in _PLATFORM_MANAGED_VARS:
                            continue
                        if any(p in var_u for p in _CONFIG_ECO):
                            final_env[var] = str(val)
                        elif val == "GENERATE":
                            if any(k in var_u for k in ["SECRET", "KEY", "TOKEN", "PASSWORD", "SALT"]):
                                if "ENCRYPTION_KEY" in var_u:
                                    import base64
                                    final_env[var] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
                                else:
                                    final_env[var] = secrets.token_hex(32)
                        else:
                            final_env[var] = str(val)
                    svc['env_vars'] = final_env

            return services_data

        except Exception as e:
            logger.error("Ecosystem Senate deliberation failed: %s", e)
            return services_data

    @classmethod
    def apply_intelligence_to_service(
        cls,
        service,
        scan_results: dict[str, Any],
        source_dir: str | None = None,
    ):
        """
        Applies intelligence to a specific service model instance.

        When source_dir is provided, uses the manifest-backed resolver
        (reading actual .env.example files) instead of the AI Senate.
        This eliminates hallucinated env vars.
        """
        from apps.deployments.models import EnvironmentVariable  # type: ignore[attr-defined]

        # Prefer manifest-backed resolution when source files are available
        if source_dir:
            return cls.apply_manifest_to_service(service, source_dir)

        scan_env_context = dict(scan_results.get('env_vars_context', {}))
        stack = scan_results.get('stack', '') or getattr(service, 'stack', '') or ''
        service_name = service.name

        # Patterns indicating a var needs a real production value
        _PLACEHOLDER_EXACT = {"", "{{GENERATE}}", "{{FILL_ME}}", "CHANGEME", "TODO", "YOUR_API_KEY", "YOUR_SECRET_KEY"}
        _PLACEHOLDER_PREFIX = ("REPLACE_WITH_", "YOUR_", "REPLACE_ME__")
        _PLACEHOLDER_IN = ("<CHANGE_ME",)
        _MOCK_PATTERNS = (
            "localhost", "127.0.0.1", "mock", "test_", "fake_", "example.com",
            "sk_test_", "pk_test_", "whsec_REPLACE_ME",
        )

        def _needs_real_value(val: str) -> bool:
            """Check if a value looks like a placeholder/mock that needs a real production value."""
            v = val.strip()
            if v in _PLACEHOLDER_EXACT:
                return True
            if v.startswith(_PLACEHOLDER_PREFIX):
                return True
            if any(p in v for p in _PLACEHOLDER_IN):
                return True
            if v.startswith("{{") and v.endswith("}}"):
                return True
            # Detect mock/heuristic values
            v_lower = v.lower()
            return any(pattern in v_lower for pattern in _MOCK_PATTERNS)

        # Build the full env dict from the service model (actual stored values)
        full_env = {}
        known_keys = set(scan_env_context.keys())
        for ev in service.env_vars.all():
            full_env[ev.key] = ev.value or ""
            known_keys.add(ev.key)
        # Add any scan-detected vars not yet on the service
        for k in scan_env_context:
            if k not in full_env:
                full_env[k] = ""

        # Identify which vars need filling (placeholder/mock values)
        fill_keys = set()
        for k, v in full_env.items():
            if _needs_real_value(str(v or "")):
                fill_keys.add(k)

        suggestions = {}
        if fill_keys:
            suggestions = cls.resolve_environment(full_env, stack, service_name, fill_keys=fill_keys)

        # Only accept suggestions for vars that were detected from the user's
        # code or already exist on the service with placeholder values.
        # AI-invented keys are discarded — the user didn't ask for them.
        known_keys = set(env_context.keys())
        for ev in service.env_vars.all():
            known_keys.add(ev.key)

        injected = []
        for key, val in suggestions.items():
            if key not in known_keys:
                logger.info(
                    "Senate suggested env var '%s' for %s but it was not in the "
                    "user's detected vars — discarding (AI hallucination).",
                    key, service_name,
                )
                continue
            if not re.match(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$', key):
                logger.warning("Skipping invalid env var key from AI: %s", key)
                continue
            # Validate the AI suggestion — if it's itself a placeholder,
            # don't write it to the database.
            if _needs_real_value(str(val)):
                logger.info(
                    "Senate returned placeholder value '%s' for '%s' on %s — discarding instead of writing.",
                    val, key, service_name,
                )
                # If the var looks like a secret, generate one instead.
                from apps.cloud.services.build_constants import is_secret_env_var
                if is_secret_env_var(key):
                    if "ENCRYPTION_KEY" in key.upper():
                        import base64
                        val = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
                    else:
                        val = secrets.token_hex(32)
                else:
                    continue  # skip entirely — can't provide a real value

            from apps.cloud.services.build_constants import is_secret_env_var
            ev, created = EnvironmentVariable.objects.get_or_create(
                service=service,
                key=key,
                defaults={
                    'value': val,
                    'is_secret': is_secret_env_var(key),
                    'source': 'SYSTEM'
                }
            )

            if not created:
                if getattr(ev, 'is_locked', False):
                    continue

                existing_val = str(ev.value or "").strip()
                if _needs_real_value(existing_val):
                    ev.value = val
                    ev.save()
                    injected.append(key)

        return suggestions, injected

    @classmethod
    def apply_manifest_to_service(
        cls,
        service,
        source_dir: str,
    ) -> tuple[dict[str, str], list[str]]:
        """
        Resolve env vars using the manifest-backed resolver.
        Reads actual .env.example, SECRETS-MANIFEST.yaml, and stack markers.

        Returns (resolved_env, injected_keys_list).
        """
        from apps.deployments.models import EnvironmentVariable  # type: ignore[attr-defined]

        try:
            from apps.deployments.services.manifest_env_resolver import (
                ManifestEnvResolver,
            )

            resolver = ManifestEnvResolver(
                source_dir=source_dir,
                service_name=service.name,
            )
            resolved_env = resolver.resolve_all()

            injected = []
            for key, val in resolved_env.items():
                if not val:
                    continue  # Skip unresolved vars

                # Sanitize key
                key_upper = key.strip().upper()
                if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", key_upper):
                    logger.warning("Skipping invalid env var key: %s", key_upper)
                    continue

                is_secret = bool(
                    re.search(
                        r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|"
                        r"ENCRYPTION_KEY|SIGNING_KEY)",
                        key_upper,
                    )
                )

                ev, created = EnvironmentVariable.objects.get_or_create(
                    service=service,
                    key=key_upper,
                    defaults={
                        "value": val,
                        "is_secret": is_secret,
                        "source": "MANIFEST",
                    },
                )

                if not created:
                    if getattr(ev, "is_locked", False):
                        continue
                    if not ev.value or "<CHANGE_ME" in str(ev.value):
                        ev.value = val
                        ev.save()
                        injected.append(key_upper)
                else:
                    injected.append(key_upper)

            if resolver.unresolved_vars:
                logger.warning(
                    "Unresolved manifest vars for %s: %s",
                    service.name,
                    ", ".join(resolver.unresolved_vars),
                )

            return resolved_env, injected

        except Exception as e:
            logger.error(
                "Manifest env resolution failed for %s: %s", service.name, e
            )
            return {}, []
