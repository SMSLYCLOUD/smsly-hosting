import argparse
import json
import os
import sys

import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service  # noqa: E402
from apps.deployments.views.service import ServiceViewSet  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

User = get_user_model()

def run_script(service_id, server_ids, ref, username):
    print(f"Executing real multi-deploy for service {service_id}...")
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        user, _ = User.objects.get_or_create(username=username, email=f'{username}@example.com')

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        print(f"Service {service_id} not found.")
        sys.exit(1)

    factory = APIRequestFactory()
    view = ServiceViewSet.as_view({'post': 'multi_deploy'})

    request_data = {
        'ref': ref,
        'server_ids': server_ids
    }

    request = factory.post(f'/api/v1/services/{service.id}/multi-deploy/', request_data, format='json')
    request.user = user

    print("Executing multi-deploy API view against live targets...")
    response = view(request, pk=str(service.id))

    print("\n--- Response ---")
    print(f"Status Code: {response.status_code}")
    print(json.dumps(response.data, indent=2))

    if response.status_code != 202:
        print("Failed to deploy to one or more servers.")
        sys.exit(1)

    print("\nSUCCESS! Remote deployment triggered successfully.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Trigger a real multi-server deployment.")
    parser.add_argument('--service-id', required=True, help="UUID of the Service to deploy")
    parser.add_argument('--server-ids', nargs='+', required=True, help="List of server UUIDs to deploy to")
    parser.add_argument('--ref', default='main', help="Git ref or Docker tag to deploy")
    parser.add_argument('--username', default='admin_script_user', help="Local username to execute as")

    args = parser.parse_args()
    run_script(args.service_id, args.server_ids, args.ref, args.username)
