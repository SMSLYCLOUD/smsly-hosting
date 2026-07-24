"""Lifecycle regression tests for the transfer service rollback flow.

Issue 7: Rollback did not stop the target service. After a successful
SERVICE transfer the target keeps the now-orphaned container running,
wasting CPU and creating a confusing "two live copies" state. The
rollback path must:

  1. stop and remove the container on the target via
     `_stop_target_service_on_rollback()`, then
  2. revert the env-var snapshot taken during remap via
     `_revert_target_platform_env()`, then
  3. regenerate the Caddyfile so routing points back to the source.

These tests pin the ordering and the per-method no-op rules.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import Service
from apps.deployments.models.servers import ManagedServer
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import (
    ServerTransferService,
    _safe_service_name,
)


class TransferRollbackStopsTargetServiceTests(TestCase):
    """Issue 7: rollback must stop the now-orphaned target container."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='rollback-user', email='rollback@example.com', password='x',
        )
        self.source_server = ManagedServer.objects.create(
            owner=self.user, name='rb-source',
            host='10.0.0.10', private_ip='10.0.0.10',
        )
        self.target_server = ManagedServer.objects.create(
            owner=self.user, name='rb-target',
            host='10.0.0.20', private_ip='10.0.0.20',
        )
        self.service = Service.objects.create(
            owner=self.user, name='rollback-svc', server=self.target_server,
        )
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.10',
            target_server_ip='10.0.0.20',
            status='COMPLETED',
            completed_at=timezone.now(),
            rollback_deadline=timezone.now() + timedelta(hours=12),
        )
        self.svc = ServerTransferService(self.transfer)
        self.call_order = []

    def _record(self, name):
        def _method(*args, **kwargs):
            self.call_order.append(name)
        return _method

    def _wire_stubs(self):
        self.svc.ssh = MagicMock()
        self.svc._log = MagicMock()
        self.svc._update_cloudflare_dns = MagicMock(side_effect=self._record('dns'))
        self.svc._delete_service_a_record = MagicMock(
            side_effect=self._record('delete_a_record'),
        )
        self.svc._regenerate_master_caddyfile = MagicMock(
            side_effect=self._record('caddy'),
        )
        self.svc._stop_target_service_on_rollback = MagicMock(
            side_effect=self._record('stop_target'),
        )
        self.svc._revert_target_platform_env = MagicMock(
            side_effect=self._record('revert_env'),
        )

    def test_rollback_invokes_target_stop_and_env_revert(self):
        self._wire_stubs()

        with patch('apps.deployments.services.transfer_service.PlatformConfig.load') as mock_cfg:
            mock_cfg.return_value = MagicMock(
                cloudflare_api_token='', domain='',
            )
            self.svc.rollback()

        self.assertIn('stop_target', self.call_order)
        self.assertIn('revert_env', self.call_order)
        self.assertIn('caddy', self.call_order)

    def test_target_stop_runs_before_caddyfile_regen(self):
        self._wire_stubs()

        with patch('apps.deployments.services.transfer_service.PlatformConfig.load') as mock_cfg:
            mock_cfg.return_value = MagicMock(
                cloudflare_api_token='', domain='',
            )
            self.svc.rollback()

        self.assertLess(
            self.call_order.index('stop_target'),
            self.call_order.index('caddy'),
        )
        self.assertLess(
            self.call_order.index('revert_env'),
            self.call_order.index('caddy'),
        )

    def test_target_stop_uses_ssh_docker_stop(self):
        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()

        svc._stop_target_service_on_rollback()

        safe = _safe_service_name(self.service.name)
        joined = ' '.join(
            str(c) for c in svc.ssh.exec_command.call_args_list
        )
        self.assertIn(f'docker stop {safe}', joined)
        self.assertIn(f'docker rm -f {safe}', joined)
        self.assertIn('2>/dev/null', joined)

    def test_target_stop_is_noop_for_full_transfer(self):
        self.transfer.transfer_type = 'FULL'
        self.transfer.save(update_fields=['transfer_type'])

        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()

        svc._stop_target_service_on_rollback()

        svc.ssh.exec_command.assert_not_called()

    def test_target_stop_is_noop_without_ssh(self):
        svc = ServerTransferService(self.transfer)
        svc.ssh = None
        svc._log = MagicMock()

        svc._stop_target_service_on_rollback()
        # No exception raised; the method bails out when ssh is None.

    def test_rollback_resets_service_server_to_source(self):
        self._wire_stubs()

        with patch('apps.deployments.services.transfer_service.PlatformConfig.load') as mock_cfg:
            mock_cfg.return_value = MagicMock(
                cloudflare_api_token='', domain='',
            )
            self.svc.rollback()

        self.service.refresh_from_db()
        self.assertEqual(self.service.server_id, self.source_server.id)

    def test_rollback_marks_transfer_rolled_back(self):
        self._wire_stubs()

        with patch('apps.deployments.services.transfer_service.PlatformConfig.load') as mock_cfg:
            mock_cfg.return_value = MagicMock(
                cloudflare_api_token='', domain='',
            )
            self.svc.rollback()

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, 'ROLLED_BACK')
        self.assertFalse(self.transfer.can_rollback)


# ── Issue 7 edge case: rollback for FULL transfer (destructive) ───────────


class TransferFullRollbackSkipsTargetStopTests(TestCase):
    """For FULL transfers the target is the new master — stopping it
    would tear down the new platform. The implementation skips the
    target stop and env revert for FULL.
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='full-rb', email='full-rb@example.com', password='x',
        )
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='FULL',
            source_server_ip='10.0.0.10',
            target_server_ip='10.0.0.20',
            status='COMPLETED',
            completed_at=timezone.now(),
            rollback_deadline=timezone.now() + timedelta(hours=12),
            metadata={'pre_transfer_env_vars': {'DATABASE_URL': 'foo'}},
        )
        self.svc = ServerTransferService(self.transfer)
        self.svc.ssh = MagicMock()
        self.svc._log = MagicMock()

    def test_stop_target_noop_for_full(self):
        self.svc._stop_target_service_on_rollback()
        self.svc.ssh.exec_command.assert_not_called()

    def test_revert_env_noop_for_full(self):
        self.svc._revert_target_platform_env()
        self.svc.ssh.upload_file.assert_not_called()
        self.svc.ssh.exec_command.assert_not_called()


# ── Lifecycle status flow: status transitions during rollback ────────────


class TransferRollbackStatusTransitionTests(TestCase):
    """Status transitions are pinned to the documented contract:

      * ROLLED_BACK is the terminal state after rollback.
      * can_rollback is set to False so the operator can't re-rollback.
      * target_ssh_key / target_ssh_password are cleared so the
        encrypted DB row doesn't keep the operator's secret around.
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='status-rb', email='status-rb@example.com', password='x',
        )
        self.target = ManagedServer.objects.create(
            owner=self.user, name='status-target',
            host='10.0.0.30', private_ip='10.0.0.30',
        )
        self.service = Service.objects.create(
            owner=self.user, name='status-svc', server=self.target,
        )
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.10',
            target_server_ip='10.0.0.30',
            status='COMPLETED',
            completed_at=timezone.now(),
            rollback_deadline=timezone.now() + timedelta(hours=12),
            target_ssh_key='tgt-key-should-be-cleared',
            target_ssh_password='tgt-pw-should-be-cleared',
        )

    def test_rollback_clears_target_ssh_credentials(self):
        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()
        svc._regenerate_master_caddyfile = MagicMock()
        svc._stop_target_service_on_rollback = MagicMock()
        svc._revert_target_platform_env = MagicMock()

        with patch('apps.deployments.services.transfer_service.PlatformConfig.load') as mock_cfg:
            mock_cfg.return_value = MagicMock(
                cloudflare_api_token='', domain='',
            )
            svc.rollback()

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.target_ssh_key, '')
        self.assertEqual(self.transfer.target_ssh_password, '')

    def test_rollback_rejects_non_completed_status(self):
        for status in ('PREPARING', 'UPLOADING', 'RESTORING', 'FAILED', 'ROLLED_BACK'):
            self.transfer.status = status
            self.transfer.save(update_fields=['status'])
            svc = ServerTransferService(self.transfer)
            with self.assertRaises(ValueError):
                svc.rollback()

    def test_rollback_rejects_after_deadline(self):
        self.transfer.rollback_deadline = timezone.now() - timedelta(hours=1)
        self.transfer.save(update_fields=['rollback_deadline'])
        svc = ServerTransferService(self.transfer)
        with self.assertRaises(ValueError):
            svc.rollback()

        self.transfer.refresh_from_db()
        self.assertFalse(self.transfer.can_rollback)

    def test_rollback_rejects_when_can_rollback_false(self):
        self.transfer.can_rollback = False
        self.transfer.save(update_fields=['can_rollback'])
        svc = ServerTransferService(self.transfer)
        with self.assertRaises(ValueError):
            svc.rollback()
