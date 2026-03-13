import os
import sys
import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from unittest.mock import patch, MagicMock
from apps.deployments.models_mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models_servers import ManagedServer
from apps.deployments.services.wireguard_service import WireGuardService
from apps.deployments.services.replication_service import ReplicationService

def run_simulation():
    print("Setting up mock models for VPN Mesh and DB Replication...")

    # We don't really need a user ID for everything since we only simulate it
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user, _ = User.objects.get_or_create(username='sim_user2', email='sim2@test.com')

    mesh, _ = MeshNetwork.objects.get_or_create(
        name='test-mesh-sim',
        subnet='10.100.0.0/24',
        listen_port=51820,
        interface_name='wg-sim'
    )

    server_1, _ = ManagedServer.objects.get_or_create(
        name='replica-server-1', host='1.2.3.4', owner=user
    )
    server_2, _ = ManagedServer.objects.get_or_create(
        name='replica-server-2', host='5.6.7.8', owner=user
    )

    # Clean old peers
    WireGuardPeer.objects.filter(mesh=mesh).delete()

    print("\n--- Testing WireGuardService ---")
    with patch('apps.deployments.services.wireguard_service.WireGuardService._ssh_run') as mock_ssh, \
         patch('apps.deployments.services.wireguard_service.WireGuardService._deploy_local') as mock_local:

        # Add local peer
        peer_local = WireGuardService.add_peer_to_mesh(mesh, server=None, is_local=True)
        print(f"Added local peer with IP: {peer_local.wg_address}")

        # Add remote peer
        peer_remote = WireGuardService.add_peer_to_mesh(mesh, server=server_1, is_local=False)
        print(f"Added remote peer with IP: {peer_remote.wg_address}")

        # Deploy mesh
        results = WireGuardService.deploy_full_mesh(mesh)
        print(f"Mesh Deploy Results: {results}")
        assert len(results['success']) == 2
        print("SUCCESS: WireGuard mesh simulated properly.")


    print("\n--- Testing ReplicationService ---")
    with patch('apps.deployments.services.replication_service.ReplicationService._deploy_patroni_local') as mock_patroni_local, \
         patch('apps.deployments.services.replication_service.ReplicationService._deploy_patroni_remote') as mock_patroni_remote, \
         patch('apps.deployments.services.replication_service.ReplicationService._deploy_haproxy_local') as mock_haproxy_local:

        rep_results = ReplicationService.deploy_replication(
            mesh,
            db_password='strong_db_pass',
            admin_password='strong_admin_pass',
            replication_password='strong_repl_pass'
        )

        print(f"Replication Deploy Results: {rep_results}")
        assert rep_results['haproxy'] == 'OK'
        assert len(rep_results['patroni']) == 2
        print("SUCCESS: Patroni Replication simulated properly.")

if __name__ == '__main__':
    run_simulation()
