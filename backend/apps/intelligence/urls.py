"""URL configuration for AI intelligence app."""
from django.urls import path
from .views import (
    ai_providers_status,
    ai_providers_update,
    ai_test_prompt,
    ai_analyze_logs,
    ai_cost_estimate,
    ai_intelligence_report,
    ai_anomaly_history,
    ai_chat_completions,
    ai_chat_stream,
)

urlpatterns = [
    path('providers/', ai_providers_status, name='ai-providers-status'),
    path('providers/update/', ai_providers_update, name='ai-providers-update'),
    path('test/', ai_test_prompt, name='ai-test-prompt'),
    path('analyze/', ai_analyze_logs, name='ai-analyze-logs'),
    path('cost-estimate/', ai_cost_estimate, name='ai-cost-estimate'),
    path('report/', ai_intelligence_report, name='ai-intelligence-report'),
    path('anomalies/', ai_anomaly_history, name='ai-anomaly-history'),
    path('chat/completions/', ai_chat_completions, name='ai-chat-completions'),
    path('chat/stream/', ai_chat_stream, name='ai-chat-stream'),
]
