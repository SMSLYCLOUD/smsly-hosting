"""Views for AI provider configuration and status."""
import json
import logging
import time
import uuid
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from django.db import DatabaseError
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from apps.licensing.decorators import require_tier

from .providers import (
    get_available_providers,
    get_configured_providers,
    ask_with_fallback,
    _sync_db_to_env,
    _sanitize_api_key,
)
from .analyzer import LogAnalyzer
from .cost import CostAdvisor
from apps.deployments.models_audit import AuditLog
from apps.core.auth import APIKeyAuthentication, CsrfExemptSessionAuthentication

logger = logging.getLogger(__name__)


def _json_safe(value, fallback):
    """Coerce arbitrary values to JSON-safe structures."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        return fallback


def _parse_int(value, default):
    """Safely parse an integer, returning default on failure."""
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_providers_status(request):
    """
    Return all AI providers with config status, model, and balance.

    GET /api/v1/ai/providers/
    Query params:
      - include_balance=true  (optional, slower — hits each provider's billing API)
    """
    include_balance = request.query_params.get("include_balance", "").lower() == "true"
    degraded_reason = None
    try:
        providers = get_available_providers(include_balance=include_balance)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch AI provider statuses: %s", exc)
        providers = []
        degraded_reason = "provider_status_unavailable"

    try:
        configured = get_configured_providers()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to resolve configured AI providers: %s", exc)
        configured = []
        degraded_reason = degraded_reason or "configured_provider_lookup_failed"

    mode = "mock"
    if len(configured) >= 2:
        mode = "senate_committee"
    elif len(configured) == 1:
        mode = "solo"

    member_names = [p.name() for p in configured] if configured else []

    payload = {
        "providers": providers,
        "mode": mode,
        "mode_label": {
            "mock": "Mock AI (no providers configured)",
            "solo": f"Solo Mode ({member_names[0] if member_names else 'N/A'})",
            "senate_committee": f"Senate Committee ({' + '.join(member_names)})",
        }.get(mode, mode),
        "active_count": len(configured),
        "total_available": len(providers),
        "senate_enabled": os.environ.get("SENATE_ENABLED", "True").lower() == "true",
        "senate_max_members": _parse_int(os.environ.get("SENATE_MAX_MEMBERS"), 5),
    }
    if degraded_reason:
        payload["degraded"] = True
        payload["degraded_reason"] = degraded_reason
    return Response(payload)


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def ai_providers_update(request):
    """
    Update AI provider settings (admin only).

    POST /api/v1/ai/providers/update/
    Body: { "openai_api_key": "sk-...", "openai_model": "gpt-4o", ... }
    """
    from .models import AIProviderSettings

    settings = AIProviderSettings.get_solo()

    updatable_fields = [
        "openai_api_key", "openai_model",
        "grok_api_key", "grok_model",
        "gemini_api_key", "gemini_model",
        "claude_api_key", "claude_model",
        "jules_api_key", "jules_model",
        "localllm_api_key", "localllm_model", "localllm_base_url",
        "smslycloud_api_key", "smslycloud_model",
        "deepseek_api_key", "deepseek_model",
        "senate_enabled", "senate_max_members",
    ]

    updated = []
    for field in updatable_fields:
        if field in request.data:
            raw_value = request.data[field]
            value = raw_value
            if value is None:
                value = ""
            if field.endswith("_api_key"):
                sanitized = _sanitize_api_key(value)
                # Ignore UI placeholders like "Configured key (hidden)" so they
                # do not wipe working keys.
                if str(value).strip() and not sanitized:
                    continue
                value = sanitized
            elif field.endswith("_model"):
                value = str(value).strip()
            setattr(settings, field, value)
            # Mask key for response
            if field.endswith("_api_key"):
                if value:
                    updated.append(
                        f"{field}: ****{value[-4:]}" if len(value) > 4 else f"{field}: set"
                    )
                else:
                    updated.append(f"{field}: cleared")
            else:
                updated.append(f"{field}: {value}")

    settings.save()
    _sync_db_to_env()

    return Response({
        "status": "updated",
        "fields": updated,
    })


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_test_prompt(request):
    """
    Test AI providers with a prompt.

    POST /api/v1/ai/test/
    Body: { "prompt": "What stack does a Django app use?" }
    """
    prompt = request.data.get("prompt", "Hello, are you working?")
    system_prompt = request.data.get("system_prompt", None)

    try:
        response, provider_name = ask_with_fallback(prompt, system_prompt=system_prompt)
        configured = get_configured_providers()
        mode = "senate_committee" if len(configured) >= 2 else ("solo" if len(configured) == 1 else "mock")

        return Response({
            "response": response,
            "provider": provider_name,
            "mode": mode,
            "active_count": len(configured),
        })
    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@authentication_classes([APIKeyAuthentication, CsrfExemptSessionAuthentication])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_chat_completions(request):
    """
    OpenAI-compatible chat completions endpoint.

    POST /api/v1/ai/chat/completions/
    Body: { "model": "...", "messages": [{"role": "user", "content": "..."}] }
    """
    data = request.data
    messages = data.get("messages", [])
    if not messages:
        return Response({"error": "No messages provided"}, status=400)

    # Extract prompt (last user message) and system prompt
    prompt = None
    system_prompt = None
    
    for msg in reversed(messages):
        if msg.get("role") == "user" and prompt is None:
            prompt = msg.get("content")
        elif msg.get("role") == "system" and system_prompt is None:
            system_prompt = msg.get("content")

    if not prompt:
        return Response({"error": "No user message found"}, status=400)

    try:
        response_text, provider_name = ask_with_fallback(prompt, system_prompt=system_prompt)
        
        # Format response in OpenAI style
        completion_id = f"chatcmpl-{uuid.uuid4()}"
        created_time = int(time.time())
        
        return Response({
            "id": completion_id,
            "object": "chat.completion",
            "created": created_time,
            "model": provider_name, # Use provider name as model for attribution
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text,
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": -1,
                "completion_tokens": -1,
                "total_tokens": -1
            }
        })
    except Exception as e:
        logger.exception("AI Chat completion failed: %s", e)
        return Response({"error": str(e)}, status=500)


@api_view(["POST"])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def ai_analyze_logs(request):
    """
    POST /api/v1/ai/analyze/
    Body: { "logs": "...", "context": "deployment|runtime|build" }
    Returns: { "diagnosis": "...", "issues": [...], "recommendations": [...], "provider": "..." }
    """
    logs = request.data.get("logs", "")
    context = request.data.get("context", "deployment")
    analyzer = LogAnalyzer()

    issues = analyzer.analyze_logs(logs)
    diagnosis = analyzer.generate_diagnosis(logs)

    # Try to extract provider from diagnosis string if it exists in format [Provider] ...
    provider = "Mock AI"
    if diagnosis.startswith("["):
        try:
            provider = diagnosis.split("]")[0][1:]
            diagnosis = diagnosis.split("] ", 1)[1]
        except IndexError:
            pass

    return Response({
        "diagnosis": diagnosis,
        "issues": issues,
        "recommendations": [],  # Could be populated from remediator
        "provider": provider
    })


@api_view(["POST"])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def ai_cost_estimate(request):
    """
    POST /api/v1/ai/cost-estimate/
    Body: { "cpu_cores": 2, "memory_mb": 1024, "stack": "django", "provider": "aws" }
    Returns: { "estimates": {...}, "ai_recommendations": "..." }
    """
    advisor = CostAdvisor()
    config = request.data

    estimates = advisor.estimate_monthly_cost(
        float(config.get('cpu_cores', 1)),
        float(config.get('memory_mb', 512)) / 1024
    )

    ai_recommendations = advisor.ai_cost_analysis(config)

    return Response({
        "estimates": estimates,
        "ai_recommendations": ai_recommendations
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_intelligence_report(request):
    """
    GET /api/v1/ai/report/
    Returns the latest daily intelligence report.
    """
    try:
        report = (
            AuditLog.objects
            .filter(actor="AI_REPORTER", action="DAILY_REPORT")
            .order_by("-created_at")
            .first()
        )
    except (DatabaseError, Exception):  # noqa: BLE001
        # Keep the dashboard stable even when audit storage is unavailable.
        return Response({
            "available": False,
            "message": "Intelligence report storage unavailable.",
            "total_deployments": 0,
            "failed_deployments": 0,
            "anomalies_detected": 0,
            "success_rate": 0,
        })

    if not report:
        return Response({
            "available": False,
            "message": "No report generated yet.",
            "total_deployments": 0,
            "failed_deployments": 0,
            "anomalies_detected": 0,
            "success_rate": 0,
        })

    metadata = report.metadata if isinstance(report.metadata, dict) else {}
    metadata = _json_safe(metadata, {})
    if not isinstance(metadata, dict):
        metadata = {}

    payload = {
        "available": True,
        "message": "Report loaded.",
        "total_deployments": 0,
        "failed_deployments": 0,
        "anomalies_detected": 0,
        "success_rate": 0,
    }
    payload.update(metadata)
    payload["available"] = True
    return Response(_json_safe(payload, {"available": True}))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_anomaly_history(request):
    """
    GET /api/v1/ai/anomalies/
    Returns history of detected anomalies and remediation actions.
    """
    try:
        anomalies = (
            AuditLog.objects
            .filter(actor__in=["AI_REMEDIATOR", "AI_REVIEWER"])
            .order_by("-created_at")[:50]
        )
    except (DatabaseError, Exception):  # noqa: BLE001
        return Response({"anomalies": [], "available": False})

    data = []
    for a in anomalies:
        meta = a.metadata if isinstance(a.metadata, dict) else {}
        safe_meta = _json_safe(meta, {})
        if not isinstance(safe_meta, dict):
            safe_meta = {}
        data.append({
            "id": str(a.id),
            "service_name": str(a.target or ""),
            "issue_type": str(a.action or "UNKNOWN"),
            "severity": str(safe_meta.get("severity", "WARNING")),
            "detected_at": a.created_at,
            "auto_fixed": a.action in ["SCALE_UP", "ROLLBACK", "CLEANUP"],
            "fix_result": str(safe_meta),
        })

    return Response(_json_safe({"anomalies": data, "available": True}, {"anomalies": [], "available": True}))
