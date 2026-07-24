from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService

User = get_user_model()


class Finding179WireguardMeshRejectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='fix179', email='fix179@example.com', password='x',
        )
        self.service = Service.objects.create(owner=self.user, name='fix179-svc')

    def _transfer_with_target(self, target_ip):
        return ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.10',
            target_server_ip=target_ip,
        )

    def test_rejects_first_octet_in_mesh_range(self):
        transfer = self._transfer_with_target('10.100.0.5')
        svc = ServerTransferService(transfer)
        with self.assertRaises(ValueError) as ctx:
            svc._target_is_local()
        self.assertIn('10.100.0.0/16', str(ctx.exception))

    def test_rejects_arbitrary_address_inside_mesh_subnet(self):
        transfer = self._transfer_with_target('10.100.55.200')
        svc = ServerTransferService(transfer)
        with self.assertRaises(ValueError):
            svc._target_is_local()

    def test_rejects_mesh_boundary_addresses(self):
        for ip in ('10.100.0.0', '10.100.255.255'):
            transfer = self._transfer_with_target(ip)
            svc = ServerTransferService(transfer)
            with self.assertRaises(ValueError):
                svc._target_is_local()
            transfer.delete()

    def test_allows_addresses_outside_mesh_range(self):
        transfer = self._transfer_with_target('203.0.113.10')
        svc = ServerTransferService(transfer)
        self.assertFalse(svc._target_is_local())

    def test_allows_neighbouring_subnet_not_in_mesh(self):
        transfer = self._transfer_with_target('10.101.0.5')
        svc = ServerTransferService(transfer)
        self.assertFalse(svc._target_is_local())

    def test_localhost_still_returns_true(self):
        transfer = self._transfer_with_target('127.0.0.1')
        svc = ServerTransferService(transfer)
        self.assertTrue(svc._target_is_local())
