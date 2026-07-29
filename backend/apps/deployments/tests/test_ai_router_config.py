import json

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.deployments.services.ai_router import (
    generate_ai_router_proxy_config,
    serialize_ai_router_config,
)
from apps.deployments.models import Deployment, EnvironmentVariable, Service

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ai-router-tests",
    }
}


@pytest.mark.django_db
def test_generate_ai_router_proxy_config_includes_selected_models_and_braid_alias():
    user = User.objects.create(username="router-owner")
    router = Service.objects.create(
        name="ai-router-demo",
        owner=user,
        docker_image="ghcr.io/berriai/litellm:main-stable",
    )
    phi3 = Service.objects.create(
        name="ollama-phi3-demo",
        owner=user,
        docker_image="ollama/ollama:latest",
        internal_port=11434,
    )
    embed = Service.objects.create(
        name="ollama-nomic-demo",
        owner=user,
        docker_image="ollama/ollama:latest",
        internal_port=11434,
    )
    Deployment.objects.create(service=phi3, status="ACTIVE", commit_hash="test")
    Deployment.objects.create(service=embed, status="ACTIVE", commit_hash="test")
    EnvironmentVariable.objects.create(service=phi3, key="OLLAMA_MODEL", value="phi3", is_secret=False)
    EnvironmentVariable.objects.create(service=embed, key="OLLAMA_MODEL", value="nomic-embed-text", is_secret=False)
    EnvironmentVariable.objects.create(
        service=router,
        key="AI_ROUTER_SELECTED_SERVICE_IDS",
        value=json.dumps([str(phi3.id), str(embed.id)]),
        is_secret=False,
    )

    yaml_text = generate_ai_router_proxy_config(router)

    assert "model_name: ollama/phi3" in yaml_text
    assert "model_name: ollama/nomic-embed-text" in yaml_text
    assert "model_name: braid-llm" in yaml_text
    assert "base_model: nomic-embed-text" in yaml_text


@pytest.mark.django_db
@override_settings(CACHES=TEST_CACHES)
def test_ai_router_config_endpoint_returns_detected_models_and_persists_selection():
    user = User.objects.create_user(username="router-api", password="pass1234")
    router = Service.objects.create(
        name="ai-router-api",
        owner=user,
        docker_image="ghcr.io/berriai/litellm:main-stable",
    )
    phi3 = Service.objects.create(
        name="ollama-phi3-api",
        owner=user,
        docker_image="ollama/ollama:latest",
        internal_port=11434,
    )
    qwen = Service.objects.create(
        name="ollama-qwen-api",
        owner=user,
        docker_image="ollama/ollama:latest",
        internal_port=11434,
    )
    Deployment.objects.create(service=phi3, status="ACTIVE", commit_hash="test")
    Deployment.objects.create(service=qwen, status="ACTIVE", commit_hash="test")
    EnvironmentVariable.objects.create(service=phi3, key="OLLAMA_MODEL", value="phi3", is_secret=False)
    EnvironmentVariable.objects.create(service=qwen, key="OLLAMA_MODEL", value="qwen2.5", is_secret=False)

    client = APIClient()
    client.force_authenticate(user=user)

    detail_url = f"/api/v1/services/{router.id}/ai-router-config/"
    response = client.get(detail_url)
    assert response.status_code == 200
    assert len(response.data["detected_models"]) == 2

    update_response = client.post(
        detail_url,
        {
            "api_base": "/api/v1",
            "ui_base": "/",
            "braid_alias": "braid-llm",
            "braid_enabled": True,
            "selected_service_ids": [str(phi3.id)],
        },
        format="json",
    )
    assert update_response.status_code == 200
    router.refresh_from_db()
    saved = serialize_ai_router_config(router)
    assert saved["selected_service_ids"] == [str(phi3.id)]
    assert saved["api_base"] == "/api/v1"
