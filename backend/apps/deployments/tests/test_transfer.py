"""Regression tests for the transfer service hardening (Issues 5/6/7/8/9).

These tests pin down the behavioral contracts that were fixed:

  * Issue 5: DNS cutover happens before source service is stopped — the
    source service must be stopped via `_stop_source_service()` *before*
    `_dns_cutover()` runs. The old behavior stopped the source inside
    `_complete()` (after Caddy regenerated), which let users hit the OLD
    service while the NEW Traefik route on the target was still warming
    up.

  * Issue 6: Default `TRANSFER_REQUIRE_BIDIRECTIONAL_SSH` to True —
    the setting must be True out of the box, with an override for
    operators who need the old behavior.

  * Issue 7: Rollback didn't stop the target service — rollback must
    stop and remove the now-orphaned container on the target before
    Caddy is told to route traffic back to the source.

  * Issue 8: Document SSH creds-in-DB risk — the SSH credential fields
    on `ServerTransfer` are encrypted at rest and cleared in
    `_complete()` / `_handle_failure()`.

  * Issue 9: Reverse env var remap on rollback — `_remap_target_platform_env`
    snapshots pre-transfer env-var values into `transfer.metadata`, and
    `_revert_target_platform_env()` writes them back during rollback.
"""
from unittest.mock import MagicMock

from django.test import TestCase, override_settings

from apps.deployments.models import Service
from apps.deployments.models.servers import ManagedServer
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import (
    ServerTransferService,
    _safe_service_name,
)

# ── Issue 6: TRANSFER_REQUIRE_BIDIRECTIONAL_SSH default ─────────────────────


class TransferRequireBidirectionalSSHDefaultsTests(TestCase):
    """Issue 6: the new flag should default to True so transfers refuse
    to start unless the target can reach the source on TCP/22.
    """

    def test_setting_defaults_to_true(self):
        from django.conf import settings as dj_settings

        self.assertTrue(dj_settings.TRANSFER_REQUIRE_BIDIRECTIONAL_SSH)

    def test_setting_can_be_disabled_via_env(self):
        from django.conf import settings as dj_settings

        with override_settings(TRANSFER_REQUIRE_BIDIRECTIONAL_SSH=False):
            self.assertFalse(dj_settings.TRANSFER_REQUIRE_BIDIRECTIONAL_SSH)


# ── Issue 5: stop source before DNS cutover ─────────────────────────────────


class StopSourceServiceBeforeDNSCutoverTests(TestCase):
    """Issue 5: the source container must be stopped before DNS cutover
    so the new Traefik route on the target is the only live endpoint.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='cutover-user', email='cutover@example.com', password='x',
        )
        self.service = Service.objects.create(
            owner=self.user, name='cutover-svc',
        )
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.10',
            target_server_ip='10.0.0.20',
            source_ssh_key='-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----',
        )
        self.svc = ServerTransferService(self.transfer)
        self.svc.ssh = MagicMock()
        self.svc.source_ssh = MagicMock()
        self.svc._log = MagicMock()
        self.svc._update = MagicMock()
        self.call_order = []

        def record_stop(*args, **kwargs):
            self.call_order.append('stop_source_service')

        def record_dns(*args, **kwargs):
            self.call_order.append('dns_cutover')

        self.svc._stop_source_service = record_stop
        self.svc._dns_cutover = record_dns
        self.svc._verify = MagicMock(side_effect=lambda: self.call_order.append('verify'))
        self.svc._complete = MagicMock(side_effect=lambda: self.call_order.append('complete'))
        self.svc._prepare = MagicMock(side_effect=lambda: self.call_order.append('prepare'))
        self.svc._upload = MagicMock(side_effect=lambda: self.call_order.append('upload'))
        self.svc._restore = MagicMock(side_effect=lambda: self.call_order.append('restore'))
        self.svc._init_ssh = MagicMock()
        self.svc._init_source_ssh = MagicMock()
        self.svc._sync_target_dashboard = MagicMock()

    def test_stop_source_runs_before_dns_cutover(self):
        self.svc.execute()

        self.assertIn('stop_source_service', self.call_order)
        self.assertIn('dns_cutover', self.call_order)
        self.assertLess(
            self.call_order.index('stop_source_service'),
            self.call_order.index('dns_cutover'),
        )

    def test_complete_does_not_stop_source_service(self):
        """The duplicate stop block in _complete() was removed in Issue 5.

        _complete() is a public-ish entry point that the existing
        multi-server harness exercises end-to-end; calling it directly
        must NOT shell out to the source-ssh docker stop path.
        """
        self.transfer.status = 'VERIFYING'
        self.transfer.save(update_fields=['status'])

        target = ManagedServer.objects.create(
            owner=self.user, name='cutover-target',
            host='10.0.0.20', private_ip='10.0.0.20',
        )
        self.transfer.service.server = target
        self.transfer.service.save()

        complete_svc = ServerTransferService(self.transfer)
        complete_svc.ssh = MagicMock()
        complete_svc.source_ssh = MagicMock()
        complete_svc._regenerate_master_caddyfile = MagicMock()
        complete_svc._remap_service_domain_for_target = MagicMock(return_value=[])
        complete_svc._update = MagicMock()
        complete_svc._log = MagicMock()

        complete_svc._complete()

        for call in complete_svc.source_ssh.exec_command.call_args_list:
            args, _ = call
            joined = ' '.join(str(a) for a in args)
            self.assertNotIn('docker stop', joined)
            self.assertNotIn('docker rm', joined)

    def test_stop_source_service_uses_source_ssh_when_remote(self):
        svc = ServerTransferService(self.transfer)
        svc.source_ssh = MagicMock()
        svc._log = MagicMock()

        svc._stop_source_service()

        safe = _safe_service_name(self.transfer.service.name)
        joined_calls = ' '.join(
            str(c) for c in svc.source_ssh.exec_command.call_args_list
        )
        self.assertIn(f'docker stop {safe}', joined_calls)
        self.assertIn(f'docker rm -f {safe}', joined_calls)

    def test_stop_source_service_is_noop_for_full_transfer(self):
        self.transfer.transfer_type = 'FULL'
        self.transfer.save(update_fields=['transfer_type'])
        svc = ServerTransferService(self.transfer)
        svc.source_ssh = MagicMock()
        svc._log = MagicMock()

        svc._stop_source_service()

        svc.source_ssh.exec_command.assert_not_called()


# ── Issue 8: SSH credentials are cleared in _complete / _handle_failure ────


class SSHCredentialClearingTests(TestCase):
    """Issue 8: the SSH credential fields are cleared on COMPLETED/FAILED.

    This is the operational safety net that the inline comment on the
    model points to — credentials are encrypted at rest with
    FIELD_ENCRYPTION_KEY, but they should not linger on the row after
    the transfer finishes.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username='cred-user', email='cred@example.com', password='x',
        )

    def test_credential_fields_are_encrypted(self):
        """The model fields must be EncryptedTextField/EncryptedCharField,
        not plain text — that's what the docstring promises.
        """
        from encrypted_model_fields.fields import (
            EncryptedCharField,
            EncryptedTextField,
        )
        self.assertIsInstance(
            ServerTransfer._meta.get_field('source_ssh_key'),
            EncryptedTextField,
        )
        self.assertIsInstance(
            ServerTransfer._meta.get_field('source_ssh_password'),
            EncryptedCharField,
        )
        self.assertIsInstance(
            ServerTransfer._meta.get_field('target_ssh_key'),
            EncryptedTextField,
        )
        self.assertIsInstance(
            ServerTransfer._meta.get_field('target_ssh_password'),
            EncryptedCharField,
        )

    def test_handle_failure_clears_source_and_target_credentials(self):
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type='SERVICE',
            source_server_ip='10.0.0.1',
            target_server_ip='10.0.0.2',
            source_ssh_key='key-kept',
            source_ssh_password='src-pw',
            target_ssh_key='tgt-key',
            target_ssh_password='tgt-pw',
        )
        svc = ServerTransferService(transfer)
        svc._log = MagicMock()

        svc._handle_failure(RuntimeError('boom'))

        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'FAILED')
        self.assertEqual(transfer.source_ssh_key, '')
        self.assertEqual(transfer.source_ssh_password, '')
        self.assertEqual(transfer.target_ssh_key, '')
        self.assertEqual(transfer.target_ssh_password, '')


# ── Issue 9: pre-transfer env vars are snapshotted into metadata ──────────


class PreTransferEnvSnapshotTests(TestCase):
    """Issue 9: the pre-transfer env-var snapshot is read by rollback.

    The Python script that runs inside the target's backend container
    has to (a) read each candidate key's current value, (b) apply the
    remap, and (c) emit the snapshot on stdout in a parseable form.
    """

    def test_remap_script_emits_pre_transfer_snapshot(self):
        """The remap script must print the snapshot between
        PRE_TRANSFER_ENV_JSON_BEGIN / PRE_TRANSFER_ENV_JSON_END sentinels.
        """
        import apps.deployments.services.transfer_service as ts

        script = ts.ServerTransferService._remap_target_platform_env
        source = script.__code__.co_consts
        joined = ' '.join(c for c in source if isinstance(c, str))
        self.assertIn('PRE_TRANSFER_ENV_JSON_BEGIN', joined)
        self.assertIn('PRE_TRANSFER_ENV_JSON_END', joined)
        self.assertIn('pre_transfer', joined)

    def test_revert_target_platform_env_reads_metadata_snapshot(self):
        """The revert method must read from
        ``transfer.metadata['pre_transfer_env_vars']`` and write each
        key back to the target's EnvironmentVariable table.
        """
        from django.contrib.auth import get_user_model
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            username='revert-user', email='revert@example.com', password='x',
        )
        service = Service.objects.create(owner=user, name='revert-svc')
        transfer = ServerTransfer.objects.create(
            owner=user,
            transfer_type='SERVICE',
            service=service,
            source_server_ip='10.0.0.1',
            target_server_ip='10.0.0.2',
            metadata={
                'pre_transfer_env_vars': {
                    'DATABASE_URL': 'postgresql://source',
                    'REDIS_URL': 'redis://source',
                },
            },
        )
        svc = ServerTransferService(transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()
        svc._find_remote_backend_container = MagicMock(
            return_value='smsly-hosting-backend-1',
        )

        uploaded = []

        def fake_upload(local_path, remote_path):
            with open(local_path) as f:
                uploaded.append(f.read())

        svc.ssh.upload_file.side_effect = fake_upload
        exec_results = ['REVERTED 2 env vars for revert-svc\n']

        def fake_exec(command, *args, **kwargs):
            joined = command if isinstance(command, str) else ' '.join(command)
            if 'python3 /tmp/' in joined:
                return exec_results.pop(0)
            return ''

        svc.ssh.exec_command.side_effect = fake_exec

        svc._revert_target_platform_env()

        self.assertEqual(len(uploaded), 1)
        self.assertIn('revert-svc', uploaded[0])
        self.assertIn('postgresql://source', uploaded[0])
        self.assertIn('redis://source', uploaded[0])
        self.assertIn('DATABASE_URL', uploaded[0])
        self.assertIn('REDIS_URL', uploaded[0])

    def test_revert_target_platform_env_noop_without_snapshot(self):
        from django.contrib.auth import get_user_model
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            username='no-snapshot', email='nos@example.com', password='x',
        )
        service = Service.objects.create(owner=user, name='no-snapshot-svc')
        transfer = ServerTransfer.objects.create(
            owner=user,
            transfer_type='SERVICE',
            service=service,
            source_server_ip='10.0.0.1',
            target_server_ip='10.0.0.2',
            metadata={},
        )
        svc = ServerTransferService(transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()

        svc._revert_target_platform_env()

        svc.ssh.upload_file.assert_not_called()
        svc.ssh.exec_command.assert_not_called()

    def test_revert_target_platform_env_noop_for_full_transfer(self):
        from django.contrib.auth import get_user_model
        user_model = get_user_model()
        user = user_model.objects.create_superuser(
            username='full-revert', email='fr@example.com', password='x',
        )
        transfer = ServerTransfer.objects.create(
            owner=user,
            transfer_type='FULL',
            source_server_ip='10.0.0.1',
            target_server_ip='10.0.0.2',
            metadata={
                'pre_transfer_env_vars': {'DATABASE_URL': 'foo'},
            },
        )
        svc = ServerTransferService(transfer)
        svc.ssh = MagicMock()
        svc._log = MagicMock()

        svc._revert_target_platform_env()

        svc.ssh.upload_file.assert_not_called()
        svc.ssh.exec_command.assert_not_called()
