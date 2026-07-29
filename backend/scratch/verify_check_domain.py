import os
import sys
import django
from django.test import RequestFactory
from rest_framework import status
from rest_framework import permissions

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Override CACHES and DATABASES for local test script if needed
# But usually LocMemCache is better for scratch scripts.
from django.conf import settings
if not settings.configured:
    django.setup()

# Patch CACHES to avoid Redis requirement
from django.core.cache import cache
settings.CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

from apps.deployments.views.service import ServiceViewSet
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model

User = get_user_model()

def test_check_domain_logic():
    factory = RequestFactory()
    
    # Simulate how the router calls the action with overrides
    view = ServiceViewSet.as_view(
        {'get': 'check_domain'},
        authentication_classes=[],
        permission_classes=[permissions.AllowAny]
    )
    
    # Create a test server
    try:
        user = User.objects.first()
        if not user:
            user = User.objects.create_user(username='testuser_v2', password='password')
    except Exception as e:
        print(f"Database error: {e}")
        return

    test_host = "test-node-final.example.com"
    try:
        server, created = ManagedServer.objects.get_or_create(
            host=test_host,
            defaults={'name': 'Test Node', 'owner': user}
        )
    except Exception as e:
        print(f"Failed to create ManagedServer: {e}")
        return
    
    print(f"Testing domain: {test_host}")
    request = factory.get(f'/api/v1/services/check-domain/?domain={test_host}')
    
    # Manually ensure we don't hit cache during this simple logic test if possible
    # but LocMemCache should be fine.
    
    response = view(request)
    
    print(f"Response status: {response.status_code}")
    if response.status_code == 200:
        print("SUCCESS: Managed server host authorized.")
    else:
        print(f"FAILURE: Managed server host NOT authorized. Content: {response.data if hasattr(response, 'data') else 'no data'}")
        
    # Clean up
    if created:
        server.delete()

if __name__ == "__main__":
    test_check_domain_logic()
