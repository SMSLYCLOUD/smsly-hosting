from unittest.mock import MagicMock

from apps.deployments.models import PlatformConfig, Project, ServerTransfer, Service
from apps.deployments.services.transfer_service import ServerTransferService
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()

class TransferServiceLocalDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='password123'
        )
        self.project = Project.objects.create(name='Test Project', owner=self.user)
        self.service = Service.objects.create(name='Test Service', project=self.project)

        # Configure PlatformConfig with a dummy server IP
        self.local_ip = '198.51.100.1'
        self.config = PlatformConfig.objects.create(
            domain='smsly.cloud',
            server_ip=self.local_ip,
            use_ssl=False
        )

    def test_local_source_ip_bypasses_source_ssh_init(self):
        # Create a transfer where source_server_ip equals PlatformConfig's server_ip
        # and target_server_ip is different (a remote server)
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            source_server_ip=self.local_ip,
            target_server_ip='203.0.113.2',
            transfer_type='SERVICE',
            service=self.service,
            target_ssh_password='target-pass',
        )

        service = ServerTransferService(transfer)

        # Mock target connection to avoid outbound socket connection
        service.ssh = MagicMock()

        # Verify _target_is_local returns False (target is remote) but
        # _node_api_url returns a URL based on target_server_ip.
        # This is the modern replacement for the old _init_source_ssh check.
        self.assertFalse(service._target_is_local())
        self.assertIn('203.0.113.2', service._node_api_url())


class RegisterIncomingTests(TestCase):
    """register-incoming must accept HMAC-signed syncs from unregistered sources.

    Regression 2026-10-02: nodes never carry a ManagedServer row for
    the master itself, so the "Unknown source node" hard-401 rejected
    every legitimate platform-initiated sync and all transfers died in
    pre-flight. Owner resolution already falls back to the first admin.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='incoming-admin', email='admin@example.com', password='x',
        )

    def _signed_post(self, source_ip, secret):
        import hashlib
        import hmac as hmac_mod
        import json as json_mod
        import time
        import secrets as secrets_mod
        from django.test import Client
        body = json_mod.dumps({
            'source_ip': source_ip, 'target_ip': '203.0.113.2',
            'transfer_type': 'SERVICE', 'service_name': 'svc',
        }, separators=(',', ':'), sort_keys=True).encode()
        ts, nonce = str(int(time.time())), secrets_mod.token_urlsafe(16)
        path = '/api/v1/transfers/register-incoming/'
        sig = hmac_mod.new(
            secret.encode(),
            f'POST|{path}|{ts}|{nonce}|{hashlib.sha256(body).hexdigest()}'.encode(),
            hashlib.sha256,
        ).hexdigest()
        return Client().post(path, data=body, content_type='application/json', headers={
            'X-Gateway-Signature-V2': sig,
            'X-Request-Timestamp': ts,
            'X-Request-Nonce': nonce,
            'X-SMSLY-Remote-Sync': '1',
        })

    @override_settings(GATEWAY_SECRET='platform-secret')
    def test_unregistered_source_with_valid_hmac_creates_transfer(self):
        resp = self._signed_post('198.51.100.9', 'platform-secret')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id', resp.json())


class ChunkedUploadTests(TestCase):
    """_upload_backup_to_target must ship every byte in order.

    Regression 2026-10-02: _upload only computed a /tmp path and
    logged "node will pull" — nothing ever delivered the file, so
    every transfer died with No such file in the restore step.
    """

    def test_chunks_reassemble_exactly(self):
        import base64
        import tempfile
        import os
        from apps.deployments.models.transfer import ServerTransfer
        from apps.deployments.services.transfer_service import ServerTransferService
        from django.contrib.auth import get_user_model

        payload = bytes(range(256)) * 20000  # ~5MB, multi-chunk
        fd, path = tempfile.mkstemp(suffix='.tar.gz')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
            user = get_user_model().objects.create_user(username='uploader', password='x')
            transfer = ServerTransfer.objects.create(
                owner=user, source_server_ip='198.51.100.1',
                target_server_ip='203.0.113.2', transfer_type='SERVICE',
            )
            svc = ServerTransferService(transfer)
            received = bytearray()
            calls = []

            def fake_request(action, body=None, **kwargs):
                self.assertEqual(action, 'incoming/upload-file')
                calls.append(body)
                raw = base64.b64decode(body['content_base64'])
                if body['offset'] == 0:
                    self.assertFalse(body['append'])
                    received.extend(raw)
                else:
                    self.assertTrue(body['append'])
                    self.assertEqual(body['offset'], len(received))
                    received.extend(raw)
                return {'status': 'written', 'size': len(raw)}

            svc._node_api_request = fake_request
            total = svc._upload_backup_to_target(path, '/tmp/test-upload.tar.gz')
            self.assertEqual(total, len(payload))
            self.assertEqual(bytes(received), payload)
            self.assertGreater(len(calls), 1)
        finally:
            os.unlink(path)


class SourceImageResolveTests(TestCase):
    """Backup-local tags must resolve to the source running image.

    Regression 2026-10-02: metadata carries backup/{name}:{uuid}
    (source-daemon-only); the target tried docker.io and every
    transfer failed at the image pull.
    """

    def _svc(self, name='img-svc'):
        from apps.deployments.models import Service
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username=f'{name}-owner', password='x')
        return Service.objects.create(name=name, owner=user)

    def test_backup_tag_resolves_to_running_image(self):
        from unittest.mock import MagicMock, patch
        from apps.deployments.models.transfer import ServerTransfer
        from apps.deployments.services.transfer_service import ServerTransferService
        svc = self._svc()
        transfer = ServerTransfer.objects.create(
            owner=svc.owner, source_server_ip='198.51.100.1',
            target_server_ip='203.0.113.2', transfer_type='SERVICE', service=svc,
        )
        engine = ServerTransferService(transfer)
        ctr = MagicMock()
        ctr.attrs = {'Config': {'Image': 'registry:5000/svc/img:abc123'}}
        client = MagicMock()
        client.containers.get.return_value = ctr
        with patch('apps.cloud.docker_client.get_docker_client', return_value=client):
            resolved = engine._resolve_source_registry_image('backup/img-svc:4589bba1')
        self.assertEqual(resolved, 'registry:5000/svc/img:abc123')
        client.containers.get.assert_called_once_with('img-svc')

    def test_non_backup_ref_passes_through(self):
        from unittest.mock import MagicMock, patch
        from apps.deployments.models.transfer import ServerTransfer
        from apps.deployments.services.transfer_service import ServerTransferService
        svc = self._svc(name='img-svc-2')
        transfer = ServerTransfer.objects.create(
            owner=svc.owner, source_server_ip='198.51.100.1',
            target_server_ip='203.0.113.2', transfer_type='SERVICE', service=svc,
        )
        engine = ServerTransferService(transfer)
        with patch('apps.cloud.docker_client.get_docker_client') as mock_client:
            resolved = engine._resolve_source_registry_image('registry:5000/svc/img:abc123')
        self.assertEqual(resolved, 'registry:5000/svc/img:abc123')
        mock_client.assert_not_called()

    def test_uninspectable_container_keeps_backup_tag(self):
        from unittest.mock import MagicMock, patch
        from apps.deployments.models.transfer import ServerTransfer
        from apps.deployments.services.transfer_service import ServerTransferService
        svc = self._svc(name='img-svc-3')
        transfer = ServerTransfer.objects.create(
            owner=svc.owner, source_server_ip='198.51.100.1',
            target_server_ip='203.0.113.2', transfer_type='SERVICE', service=svc,
        )
        engine = ServerTransferService(transfer)
        client = MagicMock()
        client.containers.get.side_effect = Exception('no such container')
        with patch('apps.cloud.docker_client.get_docker_client', return_value=client):
            resolved = engine._resolve_source_registry_image('backup/img-svc-3:deadbeef')
        self.assertEqual(resolved, 'backup/img-svc-3:deadbeef')


class ValidateTransferImageTests(TestCase):
    """_validate_transfer_image must accept every platform-registry address.

    Regression 2026-10-02: the regex only allowed dotted hostnames, so
    mesh-IP refs (10.100.0.1:5000/...) and short-host refs
    (registry:5000/...) failed validation and incoming/pull-image 400d
    every transfer at the image step.
    """

    def test_platform_registry_forms_accepted(self):
        from unittest.mock import patch as mock_patch
        import os
        from apps.core.views.transfer import _validate_transfer_image
        for ref in (
            'registry:5000/smsly/app:tag',
            '127.0.0.1:5000/smsly/app:tag',
            'localhost:5000/smsly/app:tag',
            'nginx:latest',
            'smsly/app:abc123',
        ):
            with self.subTest(ref=ref):
                self.assertTrue(_validate_transfer_image(ref))
        # Mesh-IP refs need the node-routable URL resolvable (env on a
        # real node; MASTER_MESH_IP here stands in for it).
        with mock_patch.dict(os.environ, {'MASTER_MESH_IP': '10.100.0.1'}):
            self.assertTrue(_validate_transfer_image(
                '10.100.0.1:5000/proj-9063c108/smsly-backoffice-web:a79fa9d'))

    def test_dangerous_refs_rejected(self):
        from apps.core.views.transfer import _validate_transfer_image
        for ref in (
            '',
            'nginx; rm -rf /',
            'img|cat /etc/passwd',
            'a b/c:d',
        ):
            with self.subTest(ref=ref):
                self.assertFalse(_validate_transfer_image(ref))
