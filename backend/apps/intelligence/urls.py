"""URL configuration for AI intelligence app."""
from django.urls import path
from .views import ai_providers_status, ai_providers_update, ai_test_prompt

urlpatterns = [
    path('providers/', ai_providers_status, name='ai-providers-status'),
    path('providers/update/', ai_providers_update, name='ai-providers-update'),
    path('test/', ai_test_prompt, name='ai-test-prompt'),
]
