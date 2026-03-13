import os
import sys
import django
import json
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory
from apps.deployments.views_servers import ManagedServerViewSet
from apps.deployments.models_servers import ManagedServer

User = get_user_model()

def run_simulation():
    print("Setting up mock environment for server connection...")
    user, _ = User.objects.get_or_create(username='server_connect_user', email='connect@example.com')

    # Setup DRF Request
    factory = APIRequestFactory()
    view = ManagedServerViewSet.as_view({'post': 'create'})

    request_data = {
        'name': 'newly-connected-server',
        'host': '10.0.0.99',
        'api_url': 'https://remote.smsly.cloud',
        'api_token': 'mock-valid-token'
    }

    request = factory.post('/api/v1/servers/', request_data, format='json')
    request.user = user

    print("Executing server connect API view...")

    # Mock requests to the remote API to pretend it has 5 services
    from unittest.mock import patch, MagicMock

    with patch('requests.get') as mock_get:

        def get_side_effect(url, *args, **kwargs):
            if url.endswith('/health'):
                return MagicMock(status_code=200)
            elif url.endswith('/services/'):
                return MagicMock(status_code=200, json=lambda: {'results': [{}, {}, {}, {}, {}]})
            return MagicMock(status_code=404)

        mock_get.side_effect = get_side_effect

        response = view(request)

        print(f"Status Code: {response.status_code}")
        assert response.status_code == 201

        server_id = response.data['id']
        print(f"Created server ID: {server_id}")

        # Wait a moment for the background thread to finish
        print("Waiting for background sync thread...")
        time.sleep(1)

        server = ManagedServer.objects.get(id=server_id)
        print(f"Server status: {server.status}")
        print(f"Server services count: {server.services_count}")

        assert server.services_count == 5
        assert server.status == 'ONLINE'

        print("\nSUCCESS! Server connection immediately fetched services count.")

if __name__ == '__main__':
    run_simulation()
