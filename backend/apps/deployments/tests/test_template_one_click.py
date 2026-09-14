"""Tests for the 1-click template fixes: LLM-only shared Ollama + visible failures."""
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.deployment.tasks_templates import (
    one_click_deploy_template_task,
)

User = get_user_model()
NS = "apps.deployments.tasks.deployment.tasks_templates"


def _user(name):
    return User.objects.create(username=name)


def _service(user, name):
    return Service.objects.create(name=name, owner=user)


def _fixture(template):
    return patch(f"{NS}.json.load", return_value=[template])


def _no_smart_deploy():
    return patch(
        "apps.deployments.tasks.deployment.tasks_deploy.enqueue_smart_deploy_task"
    )


def _docker_manifest_ok():
    """Neutralize the registry probe: real `docker manifest inspect` is
    rate-limited/flaky and must never decide unit-test outcomes."""
    good = MagicMock()
    good.returncode = 0
    good.stdout = "{}"
    good.stderr = ""
    return patch(
        "apps.deployments.tasks.deployment.tasks_templates.subprocess.run",
        return_value=good,
    )


def _plain_template(**overrides):
    tpl = {
        "id": "wordpress",
        "docker_image": "wordpress:latest",
        "env_vars": [{"key": "DEBUG", "value": "false"}],
        "required_addons": [],
    }
    tpl.update(overrides)
    return tpl


@pytest.mark.django_db
def test_shared_ollama_not_created_for_plain_template():
    """Non-LLM templates must NOT auto-deploy ollama-cpp-shared."""
    user = _user("tmpl-plain")
    service = _service(user, "tmpl-plain-svc")
    with _fixture(_plain_template()), _no_smart_deploy(), _docker_manifest_ok(), patch(
        f"{NS}._ensure_shared_ollama_cpp"
    ) as mock_ensure:
        one_click_deploy_template_task(str(service.id), "wordpress")
    mock_ensure.assert_not_called()
    assert not Service.objects.filter(name__startswith="ollama-cpp-shared").exists()


@pytest.mark.django_db
def test_shared_ollama_created_for_ollama_image_template():
    """Ollama-native images qualify as LLM templates."""
    user = _user("tmpl-ollama-img")
    service = _service(user, "tmpl-ollama-img-svc")
    tpl = _plain_template(id="deepseek-r1", docker_image="ollama/ollama:latest")
    with _fixture(tpl), _no_smart_deploy(), _docker_manifest_ok(), patch(
        f"{NS}._ensure_shared_ollama_cpp", return_value=None
    ) as mock_ensure, patch(
        f"{NS}._pull_ollama_models_into_shared"
    ):
        one_click_deploy_template_task(str(service.id), "deepseek-r1")
    mock_ensure.assert_called_once()


@pytest.mark.django_db
def test_shared_ollama_created_for_ollama_env_ref():
    """OLLAMA_MODEL/OLLAMA_BASE_URL references qualify as LLM templates."""
    user = _user("tmpl-ollama-env")
    service = _service(user, "tmpl-ollama-env-svc")
    tpl = _plain_template(
        id="my-llm-app",
        docker_image="myorg/my-llm-app:latest",
        env_vars=[{"key": "OLLAMA_MODEL", "value": "llama3"}],
    )
    with _fixture(tpl), _no_smart_deploy(), _docker_manifest_ok(), patch(
        f"{NS}._ensure_shared_ollama_cpp", return_value=None
    ) as mock_ensure, patch(
        f"{NS}._pull_ollama_models_into_shared"
    ):
        one_click_deploy_template_task(str(service.id), "my-llm-app")
    mock_ensure.assert_called_once()


@pytest.mark.django_db
def test_shared_ollama_created_for_ai_router():
    """ai-router always wires OLLAMA_BASE_URL downstream, so it qualifies."""
    user = _user("tmpl-airouter")
    service = _service(user, "tmpl-airouter-svc")
    tpl = _plain_template(id="ai-router", docker_image="acme/ai-router:latest")
    with _fixture(tpl), _no_smart_deploy(), _docker_manifest_ok(), patch(
        f"{NS}._ensure_shared_ollama_cpp", return_value=None
    ), patch(
        f"{NS}._pull_ollama_models_into_shared"
    ), patch(
        "apps.deployments.tasks.Addon.objects.filter"
    ) as mock_addon_filter, patch(
        "apps.deployments.tasks.smart_deploy_task.delay"
    ):
        mock_addon_filter.return_value.all.return_value = []
        mock_addon_filter.return_value.first.return_value = None
        # Must not raise; ai-router block runs with shared_ollama_id=None.
        one_click_deploy_template_task(str(service.id), "ai-router")


@pytest.mark.django_db
def test_addon_failure_creates_failed_row_not_silent():
    """Failed addon provisioning must land a FAILED row, not vanish."""
    user = _user("tmpl-addonfail")
    service = _service(user, "tmpl-addonfail-svc")
    tpl = _plain_template(required_addons=["POSTGRES"])
    with _fixture(tpl), _no_smart_deploy(), _docker_manifest_ok(), patch(
        f"{NS}.addon_provisioner"
    ) as mock_prov:
        mock_prov.ADDON_IMAGES = {"POSTGRES": "postgres:16"}
        mock_prov.provision_dispatch.side_effect = RuntimeError("no space")
        one_click_deploy_template_task(str(service.id), "wordpress")
    failed = Deployment.objects.filter(
        service=service, status=Deployment.Status.FAILED
    )
    assert failed.count() == 1
    assert "POSTGRES" in (failed.first().build_logs or "")


@pytest.mark.django_db
def test_image_verify_failure_creates_failed_row_and_raises():
    """Unpullable images fail the task AND leave a visible FAILED row."""
    user = _user("tmpl-badimg")
    service = _service(user, "tmpl-badimg-svc")
    tpl = _plain_template(docker_image="invalid.example.invalid/nope:1")
    bad = MagicMock()
    bad.returncode = 1
    bad.stderr = "no such image"
    with _fixture(tpl), _no_smart_deploy(), patch(
        f"{NS}.subprocess.run", return_value=bad
    ):
        with pytest.raises(RuntimeError):
            one_click_deploy_template_task(str(service.id), "wordpress")
    failed = Deployment.objects.filter(
        service=service, status=Deployment.Status.FAILED
    )
    assert failed.count() == 1
