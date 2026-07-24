from types import SimpleNamespace
from unittest.mock import patch

import django.test
from django.test import override_settings

from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService


class TransferStrictModeTests(django.test.TestCase):
    def test_execute_fails_when_stub_disabled(self):
        transfer = ServerTransfer.objects.create(
            source_server_ip="10.0.0.1",
            target_server_ip="10.0.0.2",
            transfer_type="SERVICE",
        )

        with override_settings(ALLOW_STUB_TRANSFER_PIPELINE=False):
            svc = ServerTransferService(transfer)
            svc.execute()

        transfer.refresh_from_db()
        self.assertEqual(transfer.status, "FAILED")
        self.assertNotIn("not implemented", transfer.error_message.lower())

    @patch("apps.deployments.services.transfer_service.PlatformConfig.load")
    def test_restored_service_run_command_pins_traefik_network(self, mock_config):
        mock_config.return_value = SimpleNamespace(use_ssl=False)
        transfer = SimpleNamespace()
        service = SimpleNamespace(
            name="smsly-frontend-local",
            docker_image="backup/smsly-frontend-local:latest",
            public_domain="smsly-frontend-local.grid.smsly.cloud",
            internal_port=3000,
        )
        metadata = {"docker_image": "backup/smsly-frontend-local:latest", "env_vars": []}

        command = ServerTransferService(transfer)._generate_docker_run_command(service, metadata)

        self.assertIn("traefik.docker.network=smsly-net", command)
        self.assertIn("traefik.http.routers.smsly-frontend-local.service=smsly-frontend-local", command)
        self.assertIn("traefik.http.services.smsly-frontend-local.loadbalancer.server.port=3000", command)
