import uuid
import pytest
from unittest.mock import patch

from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.deployments.models_mesh import MeshNetwork, WireGuardPeer

User = get_user_model()


@pytest.fixture
def admin_user():
    return User.objects.create_superuser("admin@example.com", "password")


@pytest.fixture
def auth_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def mesh():
    mesh = MeshNetwork.objects.create(name="test-mesh", subnet="10.100.0.0/24")
    # Peer 1 (Local)
    WireGuardPeer.objects.create(
        mesh=mesh, is_local=True, is_active=True, wg_address="10.100.0.1"
    )
    # Peer 2 (Remote)
    WireGuardPeer.objects.create(
        mesh=mesh, is_local=False, is_active=True, wg_address="10.100.0.2"
    )
    return mesh


@pytest.mark.django_db
@patch("subprocess.run")
@patch("apps.deployments.services.wireguard_service.WireGuardService._ssh_run")
@patch("django.core.cache.cache.get")
@patch("django.core.cache.cache.set")
@patch("django.core.cache.cache.add")
def test_preflight_check_success(mock_cache_add, mock_cache_set, mock_cache_get, mock_ssh, mock_ping, auth_client, mesh):
    # Mock cache for ratelimit bypass
    mock_cache_add.return_value = True
    mock_cache_get.return_value = None

    # Mock successful ping
    mock_ping.return_value = None

    # Pre-flight request for the remote node
    url = reverse("replication-preflight")
    payload = {
        "mesh_id": str(mesh.id),
        "target_wg_address": "10.100.0.2",
    }

    with patch("rest_framework.throttling.SimpleRateThrottle.allow_request", return_value=True):
        response = auth_client.post(url, payload, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "ok"


@pytest.mark.django_db
@patch("apps.deployments.services.replication_service.ReplicationService.deploy_replication")
@patch("django.core.cache.cache.get")
@patch("django.core.cache.cache.set")
@patch("django.core.cache.cache.add")
def test_connect_replica_success(mock_cache_add, mock_cache_set, mock_cache_get, mock_deploy, auth_client, mesh):
    # Mock cache for ratelimit bypass
    mock_cache_add.return_value = True
    mock_cache_get.return_value = None

    url = reverse("replication-connect-replica")
    payload = {
        "mesh_id": str(mesh.id),
        "target_wg_address": "10.100.0.2",
        "db_password": "strong-db-password",
        "admin_password": "strong-admin-password",
        "replication_password": "unique-repl-pass",
    }

    with patch("rest_framework.throttling.SimpleRateThrottle.allow_request", return_value=True):
        response = auth_client.post(url, payload, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "Replica connected successfully"
    mock_deploy.assert_called_once()
