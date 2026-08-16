"""URL configuration for AI intelligence app.

Naming convention: URL names use kebab-case (e.g. 'ai-providers-status').
"""
from django.urls import path

from ..views import (
    ai_analyze_logs,
    ai_anomaly_history,
    ai_chat_completions,
    ai_chat_stream,
    ai_cost_estimate,
    ai_intelligence_report,
    ai_provider_fetch_models,
    ai_providers_status,
    ai_providers_update,
    ai_test_prompt,
    jules_fix_history,
)

urlpatterns = [
    path('providers/', ai_providers_status, name='ai-providers-status'),
    path('providers/update/', ai_providers_update, name='ai-providers-update'),
    path('providers/fetch-models/', ai_provider_fetch_models, name='ai-provider-fetch-models'),
    path('test/', ai_test_prompt, name='ai-test-prompt'),
    path('analyze/', ai_analyze_logs, name='ai-analyze-logs'),
    path('cost-estimate/', ai_cost_estimate, name='ai-cost-estimate'),
    path('report/', ai_intelligence_report, name='ai-intelligence-report'),
    path('anomalies/', ai_anomaly_history, name='ai-anomaly-history'),
    path('chat/completions/', ai_chat_completions, name='ai-chat-completions'),
    path('chat/stream/', ai_chat_stream, name='ai-chat-stream'),
    path('jules-history/<uuid:service_id>/', jules_fix_history, name='jules-fix-history'),
]
