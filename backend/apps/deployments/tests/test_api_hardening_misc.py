import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.deployments.models import Service, Project
from apps.deployments.models_servers import ManagedServer

User = get_user_model()

@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser("admin_test", "admin@test.com", "password")

@pytest.fixture
def normal_user(db):
    return User.objects.create_user("normal_test", "normal@test.com", "password")

@pytest.fixture
def auth_client(normal_user):
    client = APIClient()
    client.force_authenticate(user=normal_user)
    return client

@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client

def test_unauthorized_users_cannot_access_server_backups(auth_client, admin_client, db):
    # Server backups require admin privileges
    url = reverse('server-backup-list')

    # Normal user should be forbidden
    response = auth_client.get(url)
    assert response.status_code == 403

    # Admin should be allowed
    response = admin_client.get(url)
    assert response.status_code == 200
