import django.test
from django.test import override_settings

from apps.deployments.models_transfer import ServerTransfer
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
