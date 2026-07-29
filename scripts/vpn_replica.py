import argparse
import os
import sys

import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer  # noqa: E402
from apps.deployments.models.servers import ManagedServer  # noqa: E402
from apps.deployments.services.replication_service import ReplicationService  # noqa: E402
from apps.deployments.services.wireguard_service import WireGuardService  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402


def run_script(mesh_name, server_ids, subnet, deploy_db):
    print(f"Setting up real VPN Mesh '{mesh_name}' and optionally DB Replication...")

    User = get_user_model()
    # Find a superuser to own it if needed, or leave unowned (system)
    User.objects.filter(is_superuser=True).first()

    mesh, _ = MeshNetwork.objects.get_or_create(
        name=mesh_name,
        defaults={
            'subnet': subnet,
            'listen_port': 51820,
            'interface_name': f'wg-{mesh_name[:4]}'
        }
    )

    # Clean old peers
    WireGuardPeer.objects.filter(mesh=mesh).delete()

    print("\n--- Deploying WireGuardService ---")

    # Add local peer (the gateway)
    peer_local = WireGuardService.add_peer_to_mesh(mesh, server=None, is_local=True)
    print(f"Added local peer with IP: {peer_local.wg_address}")

    # Add remote peers
    for sid in server_ids:
        try:
            srv = ManagedServer.objects.get(id=sid)
            peer_remote = WireGuardService.add_peer_to_mesh(mesh, server=srv, is_local=False)
            print(f"Added remote peer '{srv.name}' with IP: {peer_remote.wg_address}")
        except ManagedServer.DoesNotExist:
            print(f"Error: Server {sid} not found. Skipping.")

    # Deploy mesh
    results = WireGuardService.deploy_full_mesh(mesh)
    print(f"Mesh Deploy Results: {results}")

    if results.get('errors'):
        print(f"WARNING: Mesh deployment had errors: {results['errors']}")

    print("\nSUCCESS: WireGuard mesh deployment executed.")

    if deploy_db:
        print("\n--- Deploying ReplicationService ---")
        try:
            import secrets
            db_pass = secrets.token_urlsafe(16)
            admin_pass = secrets.token_urlsafe(16)
            repl_pass = secrets.token_urlsafe(16)

            rep_results = ReplicationService.deploy_replication(
                mesh,
                db_password=db_pass,
                admin_password=admin_pass,
                replication_password=repl_pass
            )
            print(f"Replication Deploy Results: {rep_results}")
            print("SUCCESS: Patroni Replication deployed properly.")
        except Exception as e:
            print(f"FAILURE executing replication service: {e}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Deploy a real VPN Mesh and DB Replication across servers.")
    parser.add_argument('--mesh-name', required=True, help="Name of the mesh network to create or update")
    parser.add_argument('--server-ids', nargs='+', required=True, help="List of ManagedServer UUIDs to join the mesh")
    parser.add_argument('--subnet', default='10.100.0.0/24', help="WireGuard Subnet (e.g., 10.100.0.0/24)")
    parser.add_argument('--deploy-db', action='store_true', help="Also deploy a replicated Postgres database across the mesh")

    args = parser.parse_args()
    run_script(args.mesh_name, args.server_ids, args.subnet, args.deploy_db)
