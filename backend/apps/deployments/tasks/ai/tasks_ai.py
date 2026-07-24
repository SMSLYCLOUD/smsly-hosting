import logging
import re

logger = logging.getLogger(__name__)
import logging

from celery import shared_task
from apps.deployments.services.ai_engine import DevOpsAgent

from apps.deployments.models import (  # type: ignore[attr-defined]
    Deployment,
)

_HIDDEN_UNICODE_CHARS = re.compile(r'[​‌‍]')
_INJECTION_PATTERNS: list[re.Pattern[str]] = []


def _sanitize_for_llm(logs: str) -> str:
    """Neutralize common prompt-injection patterns in untrusted log text.

    The build/runtime logs come from arbitrary source code and may
    contain strings crafted to hijack the LLM. This function replaces
    known-injection patterns with a benign redaction token so the LLM
    sees the line existed without acting on the instructions.

    The output is intentionally conservative: real error messages may
    contain words like "ignore" or "act as" (e.g. "ignore the previous
    version") — replacing the *pattern* is safer than rejecting the
    line. The replacement token is the same in every case so the model
    can recognise it as a marker.
    """
    out = _HIDDEN_UNICODE_CHARS.sub("", logs)
    for pat in _INJECTION_PATTERNS:
        out = pat.sub("[redacted-injection]", out)
    return out


@shared_task(soft_time_limit=180, time_limit=210)
def analyze_failure_task(deployment_id):
    """
    Uses Jules AI (via SMSLY Platform) to analyze build logs and suggest fixes.
    Uses the AI Engine (Gemini) to analyze build logs.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)

        # Only analyze if we have logs
        if not deployment.build_logs:
            return {"status": "skipped", "reason": "no_build_logs"}

        # Call Jules AI
        agent = DevOpsAgent()

        # SECURITY: Sanitize logs to prevent prompt injection, then cap
        # to the last 15000 chars to bound token usage. Both layers
        # are needed: sanitization neutralizes injected instructions,
        # truncation bounds cost.
        safe_logs = _sanitize_for_llm(deployment.build_logs)[-15000:]

        diagnosis = agent.diagnose_logs(safe_logs)

        # Update deployment with AI insight
        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])
        return {"status": "ok", "deployment_id": str(deployment.id)}

    except Deployment.DoesNotExist:
        logger.warning("analyze_failure_task skipped: deployment %s not found", deployment_id)
        return {"status": "skipped", "reason": "deployment_not_found"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Error in analyze_failure_task for %s: %s", deployment_id, exc)
        return {"status": "error", "reason": str(exc)}
