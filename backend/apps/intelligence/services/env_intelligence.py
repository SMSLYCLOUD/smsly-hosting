import json
import logging
import re
import secrets
from typing import Any

from apps.intelligence.providers import _cached_ask

logger = logging.getLogger(__name__)

# Vars managed by platform addon provisioning — AI Senate must NOT fill these.
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
})

class EnvironmentIntelligenceService:
    """
    Service that uses the AI Senate to intelligently fill environment variables
    based on code context and platform standards.
    """

    SYSTEM_PROMPT = (
        "You are the SMSLY AI Senate Committee. Your mission is to provide a 100% complete, "
        "production-ready environment configuration. \n"
        "RULES:\n"
        "1. EXHAUSTIVENESS: Every variable detected must have a value. Never return null or empty.\n"
        "2. SECRETS: Use 'GENERATE' for keys/tokens/passwords. We will generate high-entropy hex strings.\n"
        "3. LINKING: Use internal service names (e.g., http://service-name:PORT) for cross-service dependencies.\n"
        "4. NEVER set PORT — the platform manages it.\n"
        "5. STACK AWARENESS:\n"
        "   - For Next.js/Nuxt/frontend: only set NEXT_PUBLIC_*, NODE_ENV, and framework vars.\n"
        "     Do NOT set Django vars (ALLOWED_HOSTS, DJANGO_*, SECRET_KEY, ADMIN_EMAIL, etc.).\n"
        "   - For Django/Python: set Django-specific vars.\n"
        "   - For Node.js APIs: set NODE_ENV, HOSTNAME, framework vars.\n"
        "6. CATEGORIES:\n"
        "   - SECRET: JWT_SECRET, API_KEY, etc.\n"
        "   - SERVICE_URL: db, redis, rabbitmq, and sibling microservices.\n"
        "   - CONFIG_FLAG: DEBUG=False, ENVIRONMENT=production.\n"
        "Return valid JSON only."
    )

    @classmethod
    def resolve_environment(cls, env_context: dict[str, list[str]], stack: str = "", service_name: str = "") -> dict[str, str]:
        """
        Takes detected variables + context and returns a dictionary of suggested values.
        """
        if not env_context:
            return {}

        # Prepare the committee brief
        brief_lines = [
            f"Service Name: {service_name}",
            f"Detected Stack: {stack}\n",
            "Detected Variables and Context:"
        ]
        for var, contexts in env_context.items():
            ctx_summary = " | ".join(contexts[:2]).replace("\n", " ")
            brief_lines.append(f"- {var}: {ctx_summary}")

        prompt = (
            "Review every single environment variable below. Provide a suggested production value for each.\n"
            "REQUIREMENT: 100% coverage. Do not skip any variable.\n"
            "For SECRETS: state 'GENERATE'.\n"
            "For INTERNAL URLs: use service names.\n"
            "NEVER set PORT — the platform manages it.\n"
            f"STACK: {stack}. Only set vars appropriate for this stack.\n"
            "For standard settings: use production-safe defaults.\n\n"
            "Return a JSON object: { \"VAR_NAME\": \"value\" }\n\n"
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
                # Expanded secret detection list: includes SALT, HEADER_VALUE, and specific POLICY tokens
                is_secret = val == "GENERATE" or any(k in var_upper for k in [
                    "SECRET", "KEY", "TOKEN", "PASSWORD", "HASH", "SALT",
                    "HEADER_VALUE", "SIGNATURE", "AUTH"
                ])

                if is_secret:
                    if "ENCRYPTION_KEY" in var_upper:
                        # Fernet requires 32 url-safe base64-encoded bytes
                        import base64
                        final_env[var] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
                    else:
                        # High-entropy hex for standard secrets
                        final_env[var] = secrets.token_hex(32)
                elif "URL" in var_upper or "HOST" in var_upper:
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
            _CONFIG_PATTERNS_FB = {
                "TTL", "TIMEOUT", "SECONDS", "DAYS", "HOURS", "MINUTES",
                "MAX_", "MIN_", "LIMIT", "COUNT", "COOLDOWN",
                "CACHE_TTL", "ROTATION_", "INTERVAL", "RETRIES", "SIZE",
            }
            fallback = {}
            import base64
            for var in env_context:
                var_upper = var.upper()
                # Skip PORT entirely — platform manages it
                if "PORT" in var_upper:
                    continue
                if any(p in var_upper for p in _CONFIG_PATTERNS_FB):
                    fallback[var] = "8000"
                elif any(k in var_upper for k in ["SECRET", "KEY", "TOKEN", "PASSWORD", "SALT"]):
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
                        var_u = var.upper()
                        # Skip PORT entirely — platform manages it
                        if var_u == "PORT" or var_u.endswith("_PORT"):
                            continue
                        if any(p in var_u for p in _CONFIG_ECO):
                            final_env[var] = str(val)
                        elif val == "GENERATE" or any(k in var.upper() for k in ["SECRET", "KEY", "TOKEN"]):
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
        from apps.deployments.models import EnvironmentVariable  # type: ignore[attr-defined]  # noqa: F401

        # Prefer manifest-backed resolution when source files are available
        if source_dir:
            return cls.apply_manifest_to_service(service, source_dir)

        env_context = dict(scan_results.get('env_vars_context', {}))
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
            for pattern in _MOCK_PATTERNS:
                if pattern in v_lower:
                    return True
            return False

        # Ensure any unfilled, placeholder, or mock environment variables on the
        # service are added to env_context for AI Senate resolution.
        for ev in service.environment_variables.all():
            val_str = str(ev.value or "").strip()
            if _needs_real_value(val_str):
                if ev.key not in env_context:
                    env_context[ev.key] = [f"Unfilled required environment variable on service {service_name}"]

        suggestions = cls.resolve_environment(env_context, stack, service_name)

        injected = []
        for key, val in suggestions.items():
            if not re.match(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$', key):
                logger.warning("Skipping invalid env var key from AI: %s", key)
                continue
            ev, created = EnvironmentVariable.objects.get_or_create(
                service=service,
                key=key,
                defaults={
                    'value': val,
                    'is_secret': any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN", "PASSWORD"]),
                    'source': 'SYSTEM'
                }
            )

            if not created:
                if getattr(ev, 'is_locked', False):
                    continue

                val_str = str(ev.value or "").strip()
                if _needs_real_value(val_str):
                    ev.value = val
                    ev.save()
                    injected.append(key)
            else:
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
