import contextlib
import inspect
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService

User = get_user_model()


class Finding105PollUntilHelperTests(TestCase):
    def test_poll_until_returns_true_when_check_succeeds(self):
        attempts = {'n': 0}

        def _check():
            attempts['n'] += 1
            return attempts['n'] >= 3

        with patch('apps.deployments.services.transfer_service.time.sleep'):
            result = ServerTransferService._poll_until(_check, timeout=10.0, interval=0.001)
        self.assertTrue(result)
        self.assertEqual(attempts['n'], 3)

    def test_poll_until_returns_false_when_deadline_passes(self):
        clock = {'now': 1000.0}

        def _monotonic():
            return clock['now']

        def _sleep(seconds):
            clock['now'] += seconds

        with patch('apps.deployments.services.transfer_service.time.monotonic', side_effect=_monotonic), \
             patch('apps.deployments.services.transfer_service.time.sleep', side_effect=_sleep):
            result = ServerTransferService._poll_until(
                lambda: False, timeout=5.0, interval=1.0,
            )
        self.assertFalse(result)

    def test_poll_until_treats_exceptions_as_not_ready(self):
        attempts = {'n': 0}

        def _check():
            attempts['n'] += 1
            if attempts['n'] < 2:
                raise RuntimeError('not yet')
            return True

        with patch('apps.deployments.services.transfer_service.time.sleep'):
            result = ServerTransferService._poll_until(_check, timeout=5.0, interval=0.001)
        self.assertTrue(result)
        self.assertGreaterEqual(attempts['n'], 2)


class Finding105PrepareUsesPollHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='fix105', email='fix105@example.com', password='x',
        )
        self.service = Service.objects.create(owner=self.user, name='fix105-svc')
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='203.0.113.10',
            target_server_ip='203.0.113.20',
        )

    def test_prepare_calls_poll_until_after_install_docker(self):
        svc = ServerTransferService(self.transfer)
        svc._target_is_local = lambda: False
        svc._update = MagicMock()
        svc._find_remote_backend_container = MagicMock(return_value='backend-x')
        svc._ensure_target_platform_started = MagicMock()
        svc._wait_for_remote_backend_ready = MagicMock()

        check_calls = {'n': 0}

        def _check_docker():
            check_calls['n'] += 1
            return check_calls['n'] > 1

        svc.ssh = MagicMock()
        svc.ssh.check_docker.side_effect = _check_docker
        svc.ssh.install_docker = MagicMock()

        with patch.object(
            ServerTransferService, '_poll_until', wraps=ServerTransferService._poll_until,
        ) as poll_mock, \
             patch('apps.deployments.services.transfer_service.BackupService') as backup_mock:
            backup_mock.return_value.backup_service.return_value = MagicMock(
                file_path='/tmp/fake.tar.gz',
            )
            with contextlib.suppress(Exception):
                svc._prepare()

        poll_mock.assert_called()

    def test_prepare_source_no_longer_uses_hardcoded_sleep(self):
        src = inspect.getsource(ServerTransferService._prepare)
        self.assertNotIn('time.sleep(5)', src)
        self.assertIn('_poll_until', src)
