import logging

from apps.deployments.models import Service
from apps.deployments.utils import (
    append_log,
)

logger = logging.getLogger(__name__)


def _escalate_to_ai(deployment, service, container_logs):
    """
    Escalate an unknown runtime error to AI models with full code context.
    Uses all configured AI providers via ask_with_fallback.
    """
    try:
        from apps.intelligence.providers import ask_with_fallback

        # Build rich context: logs + service info + env vars (masked)
        env_summary = ", ".join(
            f"{ev.key}={'***' if ev.is_secret else ev.value}"
            for ev in service.env_vars.all()
        )

        prompt = (
            f"A deployed container for service '{service.name}' crashed immediately "
            f"after deployment. Analyze the logs and provide:\n"
            f"1. Root cause of the crash\n"
            f"2. Specific fix (env var to add, config to change, code to fix)\n"
            f"3. Whether this can be auto-fixed by the platform\n\n"
            f"Service: {service.name}\n"
            f"Deploy type: {service.deploy_type}\n"
            f"Image: {service.docker_image or 'built from git'}\n"
            f"Git repo: {service.repository_url}\n"
            f"Env vars: {env_summary}\n\n"
            f"--- CONTAINER LOGS (last 200 lines) ---\n"
            f"{container_logs[-4000:]}\n"
            f"--- END LOGS ---\n\n"
            f"Return a JSON object:\n"
            f'{{\n'
            f'  "root_cause": "Brief description",\n'
            f'  "fix": "Specific actionable fix",\n'
            f'  "env_vars_needed": {{"KEY": "value_or_empty"}},\n'
            f'  "auto_fixable": true/false,\n'
            f'  "severity": "critical/warning/info"\n'
            f'}}\n'
        )

        response, provider_name = ask_with_fallback(prompt)
        deployment.ai_diagnosis = response
        deployment.save(update_fields=['ai_diagnosis'])

        append_log(
            deployment,
            f"\n🤖 AI Diagnosis ({provider_name}):\n{response[:2000]}\n"
        )

        # Try to parse and auto-apply AI suggestions
        from apps.deployments.utils import parse_ai_resource_recommendation
        parsed = parse_ai_resource_recommendation(response)
        if parsed and parsed.get('env_vars_needed'):
            from apps.deployments.services.error_resolver import _apply_fix
            fix = {'env': parsed['env_vars_needed']}
            import re as _re
            action = _apply_fix(fix, _re.match('', ''), '', service, deployment)
            if action:
                append_log(deployment, f"  ✅ AI-suggested fix applied: {action}\n")

    except Exception as e:
        logger.warning("AI escalation failed for deployment %s: %s",
                       deployment.id, e)
        append_log(deployment, f"\n🤖 AI diagnosis unavailable: {e}\n")
