import json
import logging
import re
import secrets
from typing import Dict, List, Any
from apps.intelligence.providers import ask_with_fallback

logger = logging.getLogger(__name__)

class EnvironmentIntelligenceService:
    """
    Service that uses the AI Senate to intelligently fill environment variables
    based on code context and platform standards.
    """

    SYSTEM_PROMPT = (
        "You are the SMSLY AI Senate Committee. Your task is to analyze environment "
        "variables detected in a codebase and provide optimal production values. "
        "Categorize each variable as: \n"
        "- SECRET: Requires a unique random hex string (e.g., JWT_SECRET, API_KEY)\n"
        "- SERVICE_URL: Maps to internal SMSLY services (db, redis, rabbitmq, backend, identity)\n"
        "- CONFIG_FLAG: Boolean or Enum (e.g., DEBUG=False, ENVIRONMENT=production)\n"
        "- CONSTANT: Static values (e.g., PORT=8000, LOG_LEVEL=info)\n"
        "Return your resolution in valid JSON format only."
    )

    @classmethod
    def resolve_environment(cls, env_context: Dict[str, List[str]], stack: str = "", service_name: str = "") -> Dict[str, str]:
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
            "Review the following environment variables and provide suggested production values. "
            "For SECRETS, just state 'GENERATE'. For SERVICE_URLs, use internal Docker names. "
            "For standard flags like DEBUG, use 'False'. For PORT, use '8000'.\n\n"
            "Return a JSON object where keys are variable names and values are the suggested strings.\n\n"
            + "\n".join(brief_lines)
        )

        try:
            response_text, provider = ask_with_fallback(prompt, cls.SYSTEM_PROMPT)
            logger.info("Senate resolution for %s delivered by %s", service_name, provider)

            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                suggestions = json.loads(json_match.group(0))
            else:
                # Fallback: simple line-by-line parsing
                suggestions = {}
                for line in response_text.splitlines():
                    if ':' in line:
                        parts = line.split(':', 1)
                        k = parts[0].strip().strip('"').strip("'").strip("- ")
                        v = parts[1].strip().strip('"').strip("'").strip(",")
                        if k and v:
                            suggestions[k] = v

            # Post-process suggestions
            final_env = {}
            for var, val in suggestions.items():
                var_upper = var.upper()
                if val == "GENERATE" or any(k in var_upper for k in ["SECRET", "KEY", "TOKEN", "PASSWORD", "HASH"]):
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
            fallback = {}
            for var in env_context:
                if any(k in var.upper() for k in ["SECRET", "KEY", "TOKEN"]):
                    fallback[var] = secrets.token_hex(32)
                elif "PORT" in var.upper():
                    fallback[var] = "8000"
            return fallback

    @classmethod
    def resolve_ecosystem(cls, services_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
            "ENSURE CONSISTENCY: If one service is a frontend and another is a backend, map the frontend's "
            "backend URL variables (e.g. API_URL, BACKEND_URL) to the internal service name of the backend.\n\n"
            "Return a JSON object where keys are SERVICE NAMES and values are objects of their env vars.\n\n"
            "Format:\n"
            "{\n"
            "  \"service-a\": {\"VAR1\": \"val1\", \"VAR2\": \"GENERATE\"},\n"
            "  \"service-b\": {\"API_URL\": \"http://service-a:8000\"}\n"
            "}\n\n"
            + "\n".join(committee_brief)
        )

        try:
            response_text, provider = ask_with_fallback(prompt, cls.SYSTEM_PROMPT)
            logger.info("Ecosystem Senate resolution delivered by %s", provider)

            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                return services_data

            all_suggestions = json.loads(json_match.group(0))

            for svc in services_data:
                name = svc.get('name')
                if name in all_suggestions:
                    suggestions = all_suggestions[name]
                    final_env = {}
                    for var, val in suggestions.items():
                        if val == "GENERATE" or any(k in var.upper() for k in ["SECRET", "KEY", "TOKEN"]):
                            final_env[var] = secrets.token_hex(32)
                        else:
                            final_env[var] = str(val)
                    svc['env_vars'] = final_env
            
            return services_data

        except Exception as e:
            logger.error("Ecosystem Senate deliberation failed: %s", e)
            return services_data

    @classmethod
    def apply_intelligence_to_service(cls, service, scan_results: Dict[str, Any]):
        """
        Applies intelligence to a specific service model instance.
        """
        from apps.deployments.models import EnvironmentVariable
        
        env_context = scan_results.get('env_vars_context', {})
        stack = scan_results.get('stack', '')
        service_name = service.name
        
        suggestions = cls.resolve_environment(env_context, stack, service_name)
        
        # In SMSLY-HOSTING, env vars are stored in a separate table/relation
        # We need to update or create them.
        
        injected = []
        for key, val in suggestions.items():
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
