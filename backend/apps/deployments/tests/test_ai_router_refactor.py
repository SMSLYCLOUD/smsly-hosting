import os
import pytest
from unittest.mock import MagicMock, patch
from apps.deployments.tasks import one_click_deploy_template_task
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.models_addons import Addon
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.mark.django_db
def test_render_value_with_env_overrides():
    """Verify that render_value in tasks.py correctly resolves environment placeholders."""
    user = User.objects.create(username="testuser")
    service = Service.objects.create(name="test-service", owner=user, public_domain="test.smsly.cloud")
    
    # Mock template snippet
    template = {
        "id": "ai-router",
        "env_vars": [
            {"key": "AI_SENATE_URL", "value": "${AI_SENATE_URL}"},
            {"key": "CUSTOM_DOMAINS", "value": "custom.domain.com"},
            {"key": "DATABASE_URL", "value": "${DATABASE_URL}"}
        ]
    }
    
    # We need to mock the environment
    with patch.dict(os.environ, {
        "AI_SENATE_URL": "https://custom-senate.com",
        "DATABASE_URL": "postgres://user:pass@host:5432/db"
    }):
        # We need to bypass the actual deployment trigger and addon provisioning
        with patch('apps.deployments.tasks.Addon.objects.filter') as mock_addon_filter, \
             patch('apps.deployments.tasks.smart_deploy_task.delay') as mock_deploy:
            
            # Mock empty addons
            mock_addon_filter.return_value.all.return_value = []
            
            # Run the task
            one_click_deploy_template_task(str(service.id), "ai-router")
            
            # Check injected env vars
            env_vars = {ev.key: ev.value for ev in EnvironmentVariable.objects.filter(service=service)}
            
            assert env_vars["AI_SENATE_URL"] == "https://custom-senate.com"
            assert env_vars["DATABASE_URL"] == "postgres://user:pass@host:5432/db"
            assert env_vars["CUSTOM_DOMAINS"] == "custom.domain.com"
            
            # Check custom domains update on service
            service.refresh_from_db()
            assert "custom.domain.com" in service.custom_domains

@pytest.mark.django_db
def test_render_value_default_fallback():
    """Verify default fallbacks when env vars are missing."""
    user = User.objects.create(username="testuser2")
    service = Service.objects.create(name="test-service-2", owner=user)
    
    template = {
        "id": "ai-router",
        "env_vars": [
            {"key": "AI_SENATE_URL", "value": "${AI_SENATE_URL}"}
        ]
    }
    
    with patch.dict(os.environ, {}, clear=True):
        with patch('apps.deployments.tasks.Addon.objects.filter') as mock_addon_filter, \
             patch('apps.deployments.tasks.smart_deploy_task.delay'):
            
            mock_addon_filter.return_value.all.return_value = []
            
            one_click_deploy_template_task(str(service.id), "ai-router")
            
            ev = EnvironmentVariable.objects.get(service=service, key="AI_SENATE_URL")
            assert ev.value == "http://ollama:11434"
