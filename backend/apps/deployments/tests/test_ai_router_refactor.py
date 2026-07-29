import os
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.tasks.deployment.tasks_templates import one_click_deploy_template_task

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
        ],
        "required_addons": []
    }

    # We need to mock the environment
    with patch.dict(os.environ, {
        "AI_SENATE_URL": "https://custom-senate.com",
        "DATABASE_URL": "postgres://user:pass@host:5432/db"
    }):
        # We need to bypass the actual deployment trigger and addon provisioning
        with patch('apps.deployments.tasks.Addon.objects.filter') as mock_addon_filter, \
             patch('apps.deployments.tasks.smart_deploy_task.delay'), \
             patch('apps.deployments.tasks.deployment.tasks_templates.json.load', return_value=[template]):

            # Mock empty addons
            mock_addon_filter.return_value.all.return_value = []
            mock_addon_filter.return_value.first.return_value = None

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
        ],
        "required_addons": []
    }

    with patch.dict(os.environ, {}, clear=True):
        with patch('apps.deployments.tasks.Addon.objects.filter') as mock_addon_filter, \
             patch('apps.deployments.tasks.smart_deploy_task.delay'), \
             patch('apps.deployments.tasks.deployment.tasks_templates.json.load', return_value=[template]):

            mock_addon_filter.return_value.all.return_value = []
            mock_addon_filter.return_value.first.return_value = None

            one_click_deploy_template_task(str(service.id), "ai-router")

            ev = EnvironmentVariable.objects.get(service=service, key="AI_SENATE_URL")
            assert ev.value == "http://ollama:11434"


@pytest.mark.django_db
def test_ai_router_runtime_defaults_are_applied():
    user = User.objects.create(username="testrouter")
    service = Service.objects.create(name="ai-router-svc", owner=user)
    template = {
        "id": "ai-router",
        "env_vars": [
            {"key": "DISABLE_SCHEMA_UPDATE", "value": "true"},
            {"key": "NUM_WORKERS", "value": "1"},
            {"key": "OLLAMA_BASE_URL", "value": "${OLLAMA_BASE_URL}"},
            {"key": "OLLAMA_MODEL", "value": "${OLLAMA_MODEL}"},
        ],
        "required_addons": [],
    }

    with patch.dict(os.environ, {
        "OLLAMA_BASE_URL": "http://ollama.internal:11434",
        "OLLAMA_MODEL": "phi3",
    }, clear=True):
        with patch('apps.deployments.tasks.Addon.objects.filter') as mock_addon_filter, \
             patch('apps.deployments.tasks.smart_deploy_task.delay'), \
             patch('apps.deployments.tasks.deployment.tasks_templates.json.load', return_value=[template]):

            mock_addon_filter.return_value.all.return_value = []
            mock_addon_filter.return_value.first.return_value = None
            one_click_deploy_template_task(str(service.id), "ai-router")

    service.refresh_from_db()
    env_vars = {ev.key: ev.value for ev in EnvironmentVariable.objects.filter(service=service)}

    assert env_vars["DISABLE_SCHEMA_UPDATE"] == "true"
    assert env_vars["NUM_WORKERS"] == "1"
    assert env_vars["AI_ROUTER_API_BASE"] == "/api/v1"
    assert env_vars["AI_ROUTER_BRAID_ALIAS"] == "braid-llm"
    assert env_vars["AI_ROUTER_SELECTED_SERVICE_IDS"] == "[]"
    assert service.internal_port == 4000
    assert service.health_check_path == "/"
    assert service.memory_mb >= 1024
    assert float(service.cpu_cores) >= 1.0
    assert service.start_command == "--port 4000 --host 0.0.0.0"
