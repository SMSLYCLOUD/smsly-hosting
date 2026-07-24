from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.services.replication_service import ReplicationService

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
        mesh=mesh, is_local=True, is_active=True, wg_address="10.100.0.1",
        private_key="local-private", public_key="l" * 44,
    )
    # Peer 2 (Remote)
    WireGuardPeer.objects.create(
        mesh=mesh, is_local=False, is_active=True, wg_address="10.100.0.2",
        private_key="remote-private", public_key="r" * 44,
    )
    return mesh


@pytest.mark.django_db
def test_replication_configs_bind_to_wireguard_addresses(mesh):
    patroni_configs = ReplicationService.generate_patroni_compose(
        mesh,
        "db-pass",
        "admin-pass",
        "repl-pass",
    )
    local_config = patroni_configs["10.100.0.1"]
    haproxy_config = ReplicationService.generate_haproxy_config(mesh)

    assert "--listen-client-urls http://10.100.0.1:2379" in local_config
    assert 'ETCD3_HOSTS: "10.100.0.1:2379,10.100.0.2:2379"' in local_config
    assert 'PATRONI_POSTGRESQL_LISTEN: "10.100.0.1:55432"' in local_config
    assert 'PATRONI_RESTAPI_LISTEN: "10.100.0.1:8008"' in local_config
    assert "0.0.0.0:5432" not in local_config
    assert "bind 10.100.0.1:5000" in haproxy_config
    assert "server patroni1 10.100.0.1:55432" in haproxy_config
    assert "bind *:5000" not in haproxy_config


def test_helper_network_prefers_network_shared_with_socket_proxy():
    client = MagicMock()
    container = MagicMock()
    container.attrs = {
        "NetworkSettings": {
            "Networks": {
                "smsly-net": {},
                "socket-proxy": {},
            }
        }
    }
    client.containers.list.return_value = [container]

    with patch.dict("os.environ", {
        "DOCKER_HOST": "tcp://socket-proxy:2375",
        "DOCKER_NETWORK": "smsly-net",
        "DOCKER_HELPER_NETWORK": "",
    }):
        assert ReplicationService._helper_network_for_docker_host(client) == "smsly-net"


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
    mock_deploy.return_value = {"patroni": [], "haproxy": "OK"}

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
