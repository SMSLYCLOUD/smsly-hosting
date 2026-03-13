import os
import sys
import django
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory
from apps.deployments.views import ServiceViewSet
from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.models_servers import ManagedServer

User = get_user_model()

def run_simulation():
    print("Setting up mock environment for multi-deploy...")
    user, _ = User.objects.get_or_create(username='remote_deploy_user_2', email='remote2@example.com')

    service, _ = Service.objects.get_or_create(
        name='test-remote-deploy-service-2',
        owner=user,
        deploy_type='GIT',
        repository_url='https://github.com/example/repo'
    )

    EnvironmentVariable.objects.update_or_create(
        service=service, key='NODE_ENV', defaults={'value': 'production'}
    )

    server_invalid, _ = ManagedServer.objects.get_or_create(
        name='invalid-ssrf-server',
        owner=user,
        api_url='http://127.0.0.1:8080',
        host='127.0.0.1'
    )

    server_valid, _ = ManagedServer.objects.get_or_create(
        name='valid-remote-server',
        owner=user,
        api_url='https://example.smsly.cloud',
        host='example.smsly.cloud',
        api_token='mock-token'
    )

    print(f"Created service {service.id} and target servers.")

    # Setup DRF Request
    factory = APIRequestFactory()
    view = ServiceViewSet.as_view({'post': 'multi_deploy'})

    request_data = {
        'ref': 'main',
        'server_ids': [str(server_invalid.id), str(server_valid.id)]
    }

    request = factory.post(f'/api/v1/services/{service.id}/multi-deploy/', request_data, format='json')
    request.user = user

    print("Executing multi-deploy API view...")

    # Mock requests to the remote API
    from unittest.mock import patch, MagicMock

    with patch('requests.get') as mock_get, patch('requests.post') as mock_post, \
         patch('apps.deployments.tasks.smart_deploy_task.delay') as mock_task:

        # Simulate remote API response indicating service doesn't exist yet
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'results': []})

        # Simulate successful remote service creation and deploy queueing
        def post_side_effect(url, *args, **kwargs):
            if url.endswith('/services/'):
                return MagicMock(status_code=201, json=lambda: {'id': 'remote-service-uuid'})
            elif url.endswith('/env_vars/'):
                return MagicMock(status_code=201)
            elif url.endswith('/deploy/'):
                # Note: The view code checks status_code in (200, 201). 202 causes an error log!
                return MagicMock(status_code=201, json=lambda: {'id': 'remote-deploy-uuid'})
            return MagicMock(status_code=400)

        mock_post.side_effect = post_side_effect

        response = view(request, pk=str(service.id))

        print("\n--- Response ---")
        print(f"Status Code: {response.status_code}")
        print(json.dumps(response.data, indent=2))

        # Assertions
        assert response.status_code == 202
        remotes = response.data.get('remotes', [])

        # Check SSRF protection
        invalid_result = next((r for r in remotes if r['server_id'] == str(server_invalid.id)), None)
        assert invalid_result['status'] == 'error'
        assert 'HTTPS' in invalid_result['reason'] or 'Loopback' in invalid_result['reason']

        # Check Valid behavior
        valid_result = next((r for r in remotes if r['server_id'] == str(server_valid.id)), None)
        assert valid_result['status'] == 'queued'
        assert valid_result['auto_created'] == True

        print("\nSUCCESS! Remote deployment simulation completed smoothly. SSRF protections hold firm.")

if __name__ == '__main__':
    run_simulation()
