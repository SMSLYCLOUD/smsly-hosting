"""Views for AI provider configuration and status."""
import json
import logging
import os
import time
import uuid
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.models import Q, Sum
from django.http import JsonResponse, StreamingHttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from apps.core.auth import APIKeyAuthentication, CsrfExemptSessionAuthentication
from apps.core.models.audit import AuditLog
from apps.core.rate_limiting import AIAnalysisRateThrottle, AIChatRateThrottle

from ..analyzer import LogAnalyzer
from ..cost import CostAdvisor
from ..models import LLMUsage, UserAICap
from ..providers import (
    PROVIDERS,
    SENATE_COMMITTEE_COST_MULTIPLIER,
    _cached_ask,
    _sanitize_api_key,
    _sync_db_to_env,
    ask_with_fallback,
    get_available_providers,
    get_configured_providers,
)

logger = logging.getLogger(__name__)


def _json_safe(value, fallback):
    """Coerce arbitrary values to JSON-safe structures."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return fallback


def _parse_int(value, default):
    """Safely parse an integer, returning default on failure."""
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _check_user_cap(user):
    """Check the user's daily token / cost cap. Returns (ok, reason)."""
    from ..providers import _is_circuit_open  # noqa: F401  (kept for downstream use)
    cap, _ = UserAICap.objects.get_or_create(user=user)
    today = date.today()
    used = LLMUsage.objects.filter(user=user, created_at__date=today).aggregate(
        total=Sum('total_tokens'), cost=Sum('estimated_cost_usd')
    )
    total_used = used.get('total') or 0
    cost_used = used.get('cost') or 0
    if total_used and total_used >= cap.daily_token_cap:
        return False, "Daily token cap exceeded"
    if cost_used and cost_used >= cap.daily_cost_cap_usd:
        return False, "Daily cost cap exceeded"
    return True, None


def _check_user_cap_for_committee(user, _member_count: int):
    """Pre-flight cap check scaled by Senate Committee multiplier."""
    cap, _ = UserAICap.objects.get_or_create(user=user)
    today = date.today()
    used = LLMUsage.objects.filter(user=user, created_at__date=today).aggregate(
        total=Sum('total_tokens'), cost=Sum('estimated_cost_usd')
    )
    total_used = used.get('total') or 0
    cost_used = used.get('cost') or 0
    scaled_token_cap = cap.daily_token_cap // max(SENATE_COMMITTEE_COST_MULTIPLIER, 1)
    scaled_cost_cap = float(cap.daily_cost_cap_usd) / max(SENATE_COMMITTEE_COST_MULTIPLIER, 1)
    if total_used >= scaled_token_cap:
        return False, "Daily token cap exceeded (senate multiplier)"
    if cost_used >= scaled_cost_cap:
        return False, "Daily cost cap exceeded (senate multiplier)"
    return True, None


def _record_usage(user, provider_name: str, model: str, usage: dict,
                  prompt_text: str = "", response_text: str = "") -> dict:
    """Persist LLM usage to DB. Returns sanitized usage dict.

    SECURITY (Batch G): when the underlying provider does not
    return token counts in the response (the historical default
    for several of the SMSLY providers), the spend cap was
    effectively bypassed — every LLMUsage row recorded 0 tokens.
    We now fall back to a character-based heuristic (≈ 4 chars
    per token for English) so the cap is enforced even when the
    provider doesn't report usage. Callers should pass the
    actual prompt and response text so the estimate is correct.
    """
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        # ≈ 4 chars per token is the standard rough heuristic. The
        # absolute count is less important than the *relative*
        # count across requests, which is what the cap compares.
        return max(1, len(text) // 4)

    try:
        prompt_tokens = int(usage.get('prompt_tokens') or 0)
        completion_tokens = int(usage.get('completion_tokens') or 0)
        total_tokens = int(
            usage.get('total_tokens')
            or (prompt_tokens + completion_tokens)
        )
    except (TypeError, ValueError):
        prompt_tokens = completion_tokens = total_tokens = 0

    # Fall back to the heuristic when the provider gave us nothing.
    if total_tokens == 0 and (prompt_text or response_text):
        prompt_tokens = _estimate_tokens(prompt_text)
        completion_tokens = _estimate_tokens(response_text)
        total_tokens = prompt_tokens + completion_tokens

    cost_per_1k = 0.0
    try:
        if total_tokens:
            cost_per_1k = float(os.environ.get("LLM_USD_PER_1K_TOKENS", "0.002") or 0.0)
    except (TypeError, ValueError):
        cost_per_1k = 0.0
    estimated_cost = round((total_tokens / 1000.0) * cost_per_1k, 6)

    if user is not None and not getattr(user, "is_anonymous", False):
        try:
            LLMUsage.objects.create(
                user=user,
                provider=(provider_name or "unknown")[:64],
                model=(model or "")[:128],
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimated_cost,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to record LLMUsage: %s", exc)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminUser])
def ai_providers_status(request):
    """
    Return all AI providers with config status, model, and balance.

    GET /api/v1/ai/providers/
    Query params:
      - include_balance=true  (optional, slower — hits each provider's billing API)

    SECURITY: admin-only. Any authenticated user previously received the
    list of configured providers, which keys were present, and the
    configured model names — useful reconnaissance for an attacker that
    has already compromised a low-privilege account.
    """
    try:
        include_balance = request.query_params.get("include_balance", "").lower() == "true"
        degraded_reason = None
        # ``get_available_providers`` returns provider objects which are not directly JSON‑serialisable.
        # Convert each provider to a plain dict before adding to the payload.
        try:
            raw_providers = get_available_providers(include_balance=include_balance)
            providers = []
            for p in raw_providers:
                if isinstance(p, dict):
                    providers.append(p)
                elif hasattr(p, "to_dict"):
                    providers.append(p.to_dict())
                elif hasattr(p, "as_dict"):
                    providers.append(p.as_dict())
                else:
                    # Fallback: expose public attributes only.
                    providers.append({k: v for k, v in vars(p).items() if not k.startswith("_") and not callable(v)})
        except Exception as exc:
            logger.exception("Failed to fetch AI provider statuses: %s", exc)
            providers = []
            degraded_reason = "provider_status_unavailable"

        try:
            configured = get_configured_providers()
        except Exception as exc:
            logger.exception("Failed to resolve configured AI providers: %s", exc)
            configured = []
            degraded_reason = degraded_reason or "configured_provider_lookup_failed"

        mode = "unconfigured"
        if len(configured) >= 2:
            mode = "senate_committee"
        elif len(configured) == 1:
            mode = "solo"

        member_names = [p.name() for p in configured] if configured else []

        payload = {
            "providers": providers,
            "mode": mode,
            "mode_label": {
                "unconfigured": "No AI providers configured",
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
    except Exception as exc:
        logger.exception("ai_providers_status unhandled error: %s", exc)
        return Response(
            {
                "providers": [],
                "mode": "unconfigured",
                "mode_label": "No AI providers configured",
                "active_count": 0,
                "total_available": 0,
                "degraded": True,
                "degraded_reason": "ai_providers_status_internal_error",
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def ai_providers_update(request):
    """
    Update AI provider settings (admin only).

    POST /api/v1/ai/providers/update/
    Body: { "openai_api_key": "sk-...", "openai_model": "gpt-4o", ... }
    """
    from ..models import AIProviderSettings

    settings = AIProviderSettings.get_solo()

    updatable_fields = [
        "openai_api_key", "openai_model",
        "grok_api_key", "grok_model",
        "gemini_api_key", "gemini_model",
        "claude_api_key", "claude_model",
        "openrouter_api_key", "openrouter_model",
        "groq_api_key", "groq_model",
        "alibaba_api_key", "alibaba_model",
        "jules_api_key", "jules_model", "jules_base_url",
        "localllm_api_key", "localllm_model", "localllm_base_url",
        "smslycloud_api_key", "smslycloud_model",
        "deepseek_api_key", "deepseek_model",
        "freemodel_api_key", "freemodel_model", "freemodel_base_url",
        "opencode_api_key", "opencode_model", "opencode_base_url",
        "mistral_api_key", "mistral_model", "mistral_base_url",
        "nvidia_api_key", "nvidia_model", "nvidia_base_url",
        "cloudflare_api_key", "cloudflare_model", "cloudflare_base_url",
        "kimi_api_key", "kimi_model", "kimi_base_url",
        "orcarouter_api_key", "orcarouter_model", "orcarouter_base_url",
        "zenmax_api_key", "zenmax_model", "zenmax_base_url",
        "agentrouter_api_key", "agentrouter_model", "agentrouter_base_url",
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

    try:
        settings.full_clean()
    except DjangoValidationError as exc:
        return Response(
            {"error": "validation_failed", "fields": exc.message_dict},
            status=status.HTTP_400_BAD_REQUEST,
        )

    settings.save()
    _sync_db_to_env()

    return Response({
        "status": "updated",
        "fields": updated,
    })


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAdminUser])
def ai_provider_fetch_models(request):
    """
    Fetch available models from an OpenAI-compatible provider.

    POST /api/v1/ai/providers/fetch-models/
    Body: { "provider_id": "openai", "api_key": "sk-...", "base_url": "https://api.openai.com/v1" }

    Returns { "models": ["gpt-4o", "gpt-4o-mini", ...] }
    """
    provider_id = request.data.get("provider_id", "")
    api_key = request.data.get("api_key", "")
    base_url = request.data.get("base_url", "")

    if not provider_id:
        return Response({"error": "provider_id is required"}, status=status.HTTP_400_BAD_REQUEST)

    from ..providers.registry import PROVIDERS

    provider_cls = PROVIDERS.get(provider_id)
    if not provider_cls:
        return Response({"error": f"Unknown provider: {provider_id}"}, status=status.HTTP_400_BAD_REQUEST)

    instance = provider_cls()
    resolved_base_url = base_url or getattr(instance, "base_url", "")
    resolved_api_key = api_key or getattr(instance, "api_key", "")

    if not resolved_base_url:
        return Response(
            {"error": f"No base URL for {provider_id}. Set one in the Base URL field or via environment variable."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    import requests as _requests

    try:
        resp = _requests.get(
            f"{resolved_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {resolved_api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        models.sort()
        return Response({"models": models})
    except _requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        return Response(
            {"error": f"Provider returned HTTP {status_code}. Check your API key and base URL."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:
        logger.warning("Fetch models failed for %s: %s", provider_id, exc)
        return Response(
            {"error": f"Failed to reach {provider_id}: {exc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AIChatRateThrottle])
def ai_test_prompt(request):
    """
    Test AI providers with a prompt.

    POST /api/v1/ai/test/
    Body: { "prompt": "What stack does a Django app use?" }
    """
    prompt = request.data.get("prompt", "Hello, are you working?")
    system_prompt = request.data.get("system_prompt", None)

    try:
        configured = get_configured_providers()
        cap_check = (
            _check_user_cap_for_committee(request.user, len(configured))
            if len(configured) >= 2
            else _check_user_cap(request.user)
        )
        cap_ok, cap_reason = cap_check
        if not cap_ok:
            return Response(
                {"error": cap_reason, "code": "ai_cap_exceeded"},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        response, provider_name = _cached_ask(prompt, system_prompt=system_prompt, cache_bypass=True)
        mode = "senate_committee" if len(configured) >= 2 else ("solo" if len(configured) == 1 else "unconfigured")
        # SECURITY (Batch G): pass prompt + response so the spend
        # cap can estimate tokens when the provider doesn't report
        # usage. Without this, every LLMUsage row recorded 0 tokens.
        _record_usage(
            request.user, provider_name, provider_name, {},
            prompt_text=prompt, response_text=response,
        )

        return Response({
            "response": response,
            "provider": provider_name,
            "mode": mode,
            "active_count": len(configured),
        })
    except Exception:
        logger.exception("AI completion failed")
        return Response(
            {"error": "An AI service error occurred."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@authentication_classes([APIKeyAuthentication, CsrfExemptSessionAuthentication])
@permission_classes([IsAuthenticated])
@throttle_classes([AIChatRateThrottle])
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

    _sync_db_to_env()
    configured = get_configured_providers()
    cap_check = (
        _check_user_cap_for_committee(request.user, len(configured))
        if len(configured) >= 2
        else _check_user_cap(request.user)
    )
    cap_ok, cap_reason = cap_check
    if not cap_ok:
        return Response(
            {"error": cap_reason, "code": "ai_cap_exceeded"},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        response_text, provider_name, usage_info = _cached_ask(
            prompt, system_prompt=system_prompt, cache_bypass=True,
            return_usage=True,
        )
        # SECURITY (Batch G): pass prompt + response so the spend
        # cap can estimate tokens when the provider doesn't report
        # usage. Without this, every LLMUsage row recorded 0 tokens.
        recorded = _record_usage(
            request.user, provider_name, provider_name, usage_info or {},
            prompt_text=prompt, response_text=response_text,
        )

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
            "usage": recorded,
        })
    except Exception:
        logger.exception("AI Chat completion failed")
        return Response({"error": "An AI service error occurred."}, status=500)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([AIChatRateThrottle])
def ai_chat_stream(request):
    """SSE streaming endpoint for AI chat.

    SECURITY (Batch G): the endpoint now has a cap check (so a
    single user cannot pump unlimited Senate calls) and a throttle
    (so a tight loop cannot drive a worker-pool DoS). The body
    is also recorded as usage so the daily token / cost cap is
    honored for streaming requests too.
    """
    prompt = request.data.get("prompt") or request.data.get("message", "")
    system_prompt = request.data.get("system_prompt")
    provider_id = request.data.get("provider")

    if not prompt:
        return JsonResponse({"error": "Prompt is required"}, status=400)

    # Cap pre-flight: do not start a streaming response if the
    # user has already exceeded the cap. Otherwise the connection
    # would be opened, then aborted mid-stream, which is more
    # confusing than a clean 429.
    configured = get_configured_providers()
    cap_check = (
        _check_user_cap_for_committee(request.user, len(configured))
        if len(configured) >= 2
        else _check_user_cap(request.user)
    )
    cap_ok, cap_reason = cap_check
    if not cap_ok:
        return JsonResponse({"error": cap_reason, "code": "ai_cap_exceeded"}, status=429)

    def event_stream():
        try:
            _sync_db_to_env()

            provider = None
            if provider_id and provider_id != "auto":
                cls = PROVIDERS.get(provider_id)
                if cls:
                    instance = cls()
                    if instance.is_configured():
                        instance.id = provider_id
                        provider = instance

            if not provider:
                provider = configured[0] if configured else None

            accumulated = []
            if provider and hasattr(provider, 'ask_stream'):
                for chunk in provider.ask_stream(prompt, system_prompt):
                    accumulated.append(chunk)
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
            else:
                response, _ = ask_with_fallback(prompt, system_prompt, provider_id)
                accumulated.append(response)
                yield f"data: {json.dumps({'content': response})}\n\n"

            # Record usage after the stream completes so the
            # recorded prompt + response text are accurate.
            try:
                _record_usage(
                    request.user,
                    getattr(provider, "id", None) or "auto",
                    getattr(provider, "id", None) or "auto",
                    {},
                    prompt_text=prompt,
                    response_text="".join(accumulated),
                )
            except Exception:
                logger.exception("Failed to record streaming usage")

            yield "data: [DONE]\n\n"
        except Exception:
            logger.exception("AI streaming error")
            yield f"data: {json.dumps({'error': 'An AI service error occurred.'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@api_view(["POST"])
@permission_classes([IsAdminUser])
@throttle_classes([AIAnalysisRateThrottle])
def ai_analyze_logs(request):
    """
    POST /api/v1/ai/analyze/
    Body: { "logs": "...", "context": "deployment|runtime|build" }
    Returns: { "diagnosis": "...", "issues": [...], "recommendations": [...], "provider": "..." }
    """
    logs = request.data.get("logs", "")
    request.data.get("context", "deployment")
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
def ai_intelligence_report(request):
    """
    GET /api/v1/ai/report/
    Returns the latest daily intelligence report, optionally scoped to a service.
    Query params: ?service_id=<uuid>
    """
    request.query_params.get("service_id", "").strip()
    try:
        filters = Q(actor="AI_REPORTER", action="DAILY_REPORT")
        report = (
            AuditLog.objects
            .filter(filters)
            .order_by("-created_at")
            .first()
        )
    except (DatabaseError, Exception):
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
def ai_anomaly_history(request):
    """
    GET /api/v1/ai/anomalies/
    Returns history of detected anomalies and remediation actions.
    Query params: ?service_id=<uuid>
    """
    service_id = request.query_params.get("service_id", "").strip()
    try:
        qs = AuditLog.objects.filter(actor__in=["AI_REMEDIATOR", "AI_REVIEWER"])
        if service_id:
            qs = qs.filter(target__icontains=service_id)
        anomalies = qs.order_by("-created_at")[:50]
    except (DatabaseError, Exception):
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
            "auto_fixed": a.action in ["SCALE_UP", "ROLLBACK", "CLEANUP", "RESTART", "REBUILD", "DIAGNOSE"],
            "fix_result": str(safe_meta),
        })

    return Response(_json_safe({"anomalies": data, "available": True}, {"anomalies": [], "available": True}))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def jules_fix_history(request, service_id: str):
    """
    Return Jules auto-fix history for a service by scanning deployment logs.
    """
    try:
        from django.db.models import Q

        from apps.deployments.models import Deployment, Service

        # SECURITY: scope to services the caller can access (owner or
        # team member). Without this, any authenticated user could
        # read another tenant's jules events (which include snippets
        # of build logs).
        service = Service.objects.filter(
            Q(owner=request.user) |
            Q(project__team__members__user=request.user)
        ).distinct().get(id=service_id)
        deployments = Deployment.objects.filter(service=service).order_by("-updated_at")[:20]

        entries = []
        for d in deployments:
            logs = (d.build_logs or "")
            if not logs:
                continue

            jules_lines = [line for line in logs.split("\n") if "jules" in line.lower() or "auto-fix" in line.lower() or "Jules" in line]
            if not jules_lines:
                continue

            entries.append({
                "deployment_id": str(d.id),
                "branch": d.branch or "",
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "jules_events": jules_lines[-10:],  # last 10 jules-related log lines
                "fix_applied": any("PR created" in line for line in jules_lines),
                "fix_failed": any("Jules auto-fix failed" in line or "Jules fix request failed" in line for line in jules_lines),
            })

        return Response({"service_id": str(service.id), "entries": entries})
    except Service.DoesNotExist:
        return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        # SECURITY: log full traceback server-side; don't echo to caller
        logger.exception("jules_fix_history failed: %s", e)
        return Response({"error": "Internal error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
