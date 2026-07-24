from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.ai.tasks_ai import analyze_failure_task

User = get_user_model()

@pytest.mark.django_db
def test_prompt_injection_truncation():
    user = User.objects.create_user(username="test_ai")
    service = Service.objects.create(name="test_ai_service", owner=user)

    # Create a very long log payload > 15000 chars
    long_logs = "A" * 20000

    deployment = Deployment.objects.create(
        service=service,
        commit_hash="HEAD",
        status=Deployment.Status.FAILED,
        build_logs=long_logs
    )

    with patch('apps.deployments.tasks.ai.tasks_ai.DevOpsAgent') as MockAgent:
        mock_instance = MockAgent.return_value
        mock_instance.diagnose_logs.return_value = "Mocked AI Response"

        result = analyze_failure_task(str(deployment.id))

        assert result['status'] == 'ok'

        # Verify the agent was called with truncated logs
        mock_instance.diagnose_logs.assert_called_once()
        called_args = mock_instance.diagnose_logs.call_args[0]
        assert len(called_args[0]) == 15000
        assert called_args[0] == "A" * 15000
