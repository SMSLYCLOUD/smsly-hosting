import json
import logging
import re
import secrets
from typing import Any

from apps.intelligence.providers import _cached_ask

logger = logging.getLogger(__name__)

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
    def apply_intelligence_to_service(cls, service, scan_results: dict[str, Any]):
        """
        Applies intelligence to a specific service model instance.
        """
        from apps.deployments.models import EnvironmentVariable  # type: ignore[attr-defined]  # noqa: F401

        env_context = scan_results.get('env_vars_context', {})
        stack = scan_results.get('stack', '')
        service_name = service.name

        suggestions = cls.resolve_environment(env_context, stack, service_name)

        # In SMSLY-HOSTING, env vars are stored in a separate table/relation
        # We need to update or create them.

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
                    'is_secret': any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN"]),
                    'source': 'SYSTEM'
                }
            )

            # If the variable already exists, only update it if NOT locked and (empty or placeholder)
            if not created:
                if getattr(ev, 'is_locked', False):
                    continue

                if not ev.value or "<CHANGE_ME" in str(ev.value):
                    ev.value = val
                    ev.save()
                    injected.append(key)
            else:
                injected.append(key)

        return suggestions, injected
