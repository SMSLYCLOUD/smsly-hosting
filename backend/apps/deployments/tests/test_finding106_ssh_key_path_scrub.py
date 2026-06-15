from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services import transfer_service
from apps.deployments.services.transfer_service import (
    ServerTransferService,
    _scrub_ssh_key_paths,
)


User = get_user_model()


class Finding106SshKeyPathScrubTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='fix106', email='fix106@example.com', password='x',
        )
        self.service = Service.objects.create(owner=self.user, name='fix106-svc')
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.10',
            target_server_ip='10.0.0.20',
            target_ssh_key='-----BEGIN RSA PRIVATE KEY-----\nstub\n-----END RSA PRIVATE KEY-----',
        )

    def test_scrub_replaces_absolute_unix_path_with_basename(self):
        msg = "[Errno 2] No such file: '/home/smsly/.ssh/id_rsa_prod'"
        scrubbed = _scrub_ssh_key_paths(msg)
        self.assertNotIn('/home/smsly/.ssh', scrubbed)
        self.assertIn('id_rsa_prod', scrubbed)

    def test_scrub_replaces_windows_path_with_basename(self):
        msg = "could not read C:\\Users\\admin\\Documents\\secret_key.pem"
        scrubbed = _scrub_ssh_key_paths(msg)
        self.assertNotIn('\\Users\\admin', scrubbed)
        self.assertIn('secret_key.pem', scrubbed)

    def test_target_connection_error_strips_path_from_message(self):
        svc = ServerTransferService(self.transfer)
        with patch.object(transfer_service, 'SSHClient') as mock_cls:
            instance = MagicMock()
            instance.connect.side_effect = Exception(
                "auth failed reading /var/secrets/ssh/target_key"
            )
            mock_cls.return_value = instance
            with self.assertRaises(ConnectionError) as ctx:
                svc._init_ssh()
        message = str(ctx.exception)
        self.assertNotIn('/var/secrets/ssh', message)
        self.assertIn('target_key', message)

    def test_source_connection_error_strips_path_from_message(self):
        svc = ServerTransferService(self.transfer)
        self.transfer.source_ssh_key = (
            '-----BEGIN RSA PRIVATE KEY-----\nstub\n-----END RSA PRIVATE KEY-----'
        )
        with patch.object(transfer_service, 'SSHClient') as mock_cls, \
             patch.object(transfer_service.PlatformConfig, 'load', return_value=None):
            instance = MagicMock()
            instance.connect.side_effect = Exception(
                "no such file '/srv/keys/source_node.pem'"
            )
            mock_cls.return_value = instance
            with self.assertRaises(ConnectionError) as ctx:
                svc._init_source_ssh()
        message = str(ctx.exception)
        self.assertNotIn('/srv/keys', message)
        self.assertIn('source_node.pem', message)
