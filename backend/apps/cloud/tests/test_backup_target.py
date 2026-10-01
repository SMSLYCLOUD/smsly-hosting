"""Backup/restore target resolution (no docker, no DB)."""
from unittest import mock

from django.test import SimpleTestCase

from apps.cloud.services.backup_service.core import _resolve_backup_target


class ResolveBackupTargetTests(SimpleTestCase):
    def test_local_when_no_metadata_no_server(self):
        svc = mock.Mock()
        svc.name = 'local-svc'
        svc.active_target_type = None
        svc.server = None
        self.assertEqual(_resolve_backup_target(svc), (False, None, 'local'))

    def test_server_fk_fallback_when_metadata_missing(self):
        server = mock.Mock()
        server.name = 'node-1'
        server.is_primary = False
        svc = mock.Mock()
        svc.name = 'node-svc'
        svc.server = server
        with mock.patch(
                'apps.deployments.utils.target.resolve_active_execution_target',
                side_effect=ValueError('no metadata')):
            self.assertEqual(_resolve_backup_target(svc),
                             (True, server, 'server-fk'))

    def test_primary_server_means_local(self):
        server = mock.Mock()
        server.name = 'primary'
        server.is_primary = True
        svc = mock.Mock()
        svc.name = 's'
        svc.server = server
        with mock.patch(
                'apps.deployments.utils.target.resolve_active_execution_target',
                side_effect=ValueError('no metadata')):
            self.assertEqual(_resolve_backup_target(svc), (False, None, 'local'))

    def test_runtime_metadata_wins(self):
        server = mock.Mock()
        server.name = 'node-1'
        svc = mock.Mock()
        svc.name = 's'
        svc.server = None
        with mock.patch(
                'apps.deployments.utils.target.resolve_active_execution_target',
                return_value={'target_type': 'remote', 'server_obj': server}):
            self.assertEqual(_resolve_backup_target(svc),
                             (True, server, 'runtime-metadata'))
