import argparse
import os
import sys

import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service  # noqa: E402
from apps.deployments.models.transfer import ServerTransfer  # noqa: E402
from apps.deployments.services.transfer_service import ServerTransferService  # noqa: E402


def run_script(service_id, source_ip, target_ip, ssh_key, ssh_password):
    print(f"Starting real server transfer for service {service_id}...")

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        print(f"Error: Service {service_id} not found.")
        sys.exit(1)

    transfer = ServerTransfer.objects.create(
        service=service,
        source_server_ip=source_ip,
        target_server_ip=target_ip,
        target_ssh_key=ssh_key,
        target_ssh_password=ssh_password,
        transfer_type='SERVICE'
    )

    print(f"Created transfer task {transfer.id}. Executing via TransferService...")
    engine = ServerTransferService(transfer)

    try:
        engine.execute()
    except Exception as e:
        print(f"Error during transfer execution: {e}")
        import traceback
        traceback.print_exc()

    transfer.refresh_from_db()
    print("\n--- Transfer Results ---")
    print(f"Status: {transfer.status}")
    if transfer.error_message:
        print(f"Error: {transfer.error_message}")
    print(f"Progress: {transfer.progress_percent}% - {transfer.current_step}")

    if transfer.status == 'COMPLETED':
        print("\nSUCCESS! Transfer completed smoothly.")
    else:
        print("\nFAILURE! Transfer failed.")
        sys.exit(1)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Execute a real service transfer across servers.")
    parser.add_argument('--service-id', required=True, help="UUID of the Service to transfer")
    parser.add_argument('--source-ip', required=True, help="IP of the source server")
    parser.add_argument('--target-ip', required=True, help="IP of the target server")
    parser.add_argument('--ssh-key', default='', help="SSH Private Key for the target server")
    parser.add_argument('--ssh-password', default='', help="SSH Password for the target server")

    args = parser.parse_args()
    if not args.ssh_key and not args.ssh_password:
        print("Error: Must provide either --ssh-key or --ssh-password")
        sys.exit(1)

    run_script(args.service_id, args.source_ip, args.target_ip, args.ssh_key, args.ssh_password)
