"""Views for AI provider configuration and status."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from apps.licensing.decorators import require_tier

from .providers import (
    get_available_providers,
    get_configured_providers,
    ask_with_fallback,
    _sync_db_to_env,
)
from .analyzer import LogAnalyzer
from .cost import CostAdvisor
from apps.deployments.models_audit import AuditLog


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
    providers = get_available_providers(include_balance=include_balance)
    configured = get_configured_providers()

    mode = "mock"
    if len(configured) >= 2:
        mode = "senate_committee"
    elif len(configured) == 1:
        mode = "solo"

    member_names = [p.name() for p in configured] if configured else []

    return Response({
        "providers": providers,
        "mode": mode,
        "mode_label": {
            "mock": "Mock AI (no providers configured)",
            "solo": f"Solo Mode ({member_names[0] if member_names else 'N/A'})",
            "senate_committee": f"Senate Committee ({' + '.join(member_names)})",
        }.get(mode, mode),
        "active_count": len(configured),
        "total_available": len(providers),
    })


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
    ]

    updated = []
    for field in updatable_fields:
        if field in request.data:
            value = request.data[field]
            if value is None:
                value = ""
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
    report = AuditLog.objects.filter(actor="AI_REPORTER", action="DAILY_REPORT").order_by("-created_at").first()
    if report:
        return Response(report.metadata)
    return Response({"error": "No report generated yet"}, status=404)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@require_tier('pro', 'enterprise')
def ai_anomaly_history(request):
    """
    GET /api/v1/ai/anomalies/
    Returns history of detected anomalies and remediation actions.
    """
    anomalies = AuditLog.objects.filter(
        actor__in=["AI_REMEDIATOR", "AI_REVIEWER"]
    ).order_by("-created_at")[:50]

    data = []
    for a in anomalies:
        data.append({
            "id": str(a.id),
            "service_name": a.target,
            "issue_type": a.action,
            "severity": a.metadata.get("severity", "WARNING"),
            "detected_at": a.created_at,
            "auto_fixed": a.action in ["SCALE_UP", "ROLLBACK", "CLEANUP"],
            "fix_result": str(a.metadata)
        })

    return Response({"anomalies": data})
