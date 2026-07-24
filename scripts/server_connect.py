import argparse
import os
import sys
import time

import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models.servers import ManagedServer  # noqa: E402
from apps.deployments.views.servers import ManagedServerViewSet  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

User = get_user_model()

def run_script(name, host, api_url, api_token, username):
    print(f"Connecting to real remote server '{name}' at {api_url}...")
    user, _ = User.objects.get_or_create(username=username, email=f'{username}@example.com')

    factory = APIRequestFactory()
    view = ManagedServerViewSet.as_view({'post': 'create'})

    request_data = {
        'name': name,
        'host': host,
        'api_url': api_url,
        'api_token': api_token
    }

    request = factory.post('/api/v1/servers/', request_data, format='json')
    request.user = user

    print("Executing server connect API view against live target...")
    response = view(request)

    print(f"Status Code: {response.status_code}")
    if response.status_code != 201:
        print(f"Failed to connect server: {response.data}")
        sys.exit(1)

    server_id = response.data['id']
    print(f"Created server ID: {server_id}")

    print("Waiting for background sync thread...")
    time.sleep(2)

    server = ManagedServer.objects.get(id=server_id)
    print(f"Server status: {server.status}")
    print(f"Server services count: {server.services_count}")

    if server.status == 'ONLINE':
        print("\nSUCCESS! Server connection verified against live target.")
    else:
        print("\nWARNING! Server created but status is not ONLINE. Check connectivity and credentials.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Connect a remote Managed Server.")
    parser.add_argument('--name', required=True, help="Name of the server")
    parser.add_argument('--host', required=True, help="IP or hostname of the server")
    parser.add_argument('--api-url', required=True, help="Full URL to the remote server API")
    parser.add_argument('--api-token', required=True, help="Valid API token for the remote server")
    parser.add_argument('--username', default='admin_script_user', help="Local username to own the server")

    args = parser.parse_args()
    run_script(args.name, args.host, args.api_url, args.api_token, args.username)
