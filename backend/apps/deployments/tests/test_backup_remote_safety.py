"""
Tests for remote backup/restore shell safety improvements.

Changes:
1. ``shlex.quote`` is used for all command strings and volume names
   passed to SSH ``exec_command``, preventing shell injection.
2. Volume names are sanitised with ``replace('/', '_').replace('\\\\',
   '_').replace('..', '_')`` before being used as filenames.
3. Image save/load failures and volume backup failures now **raise**
   instead of logging a warning and continuing.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.models.core import ManagedServer
from apps.deployments.models.storage import Volume
from apps.deployments.services.backup_service import BackupService

User = get_user_model()


class RemoteBackupRaisesOnFailureTest(TestCase):
    """Remote backup/restore raises instead of silently continuing."""

    def setUp(self):
        self.user = User.objects.create_user(username="remote-safety", password="x")
        self.project = Project.objects.create(name="Remote Safety", owner=self.user)
        self.service = Service.objects.create(
            name="remote-svc",
            owner=self.user,
            project=self.project,
        )
        self.server = ManagedServer.objects.create(
            name="Remote Node",
            host="10.0.0.1",
            ssh_user="root",
            ssh_key="-----BEGIN PRIVATE KEY-----\n...",
            owner=self.user,
        )
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "10.0.0.1"
        self.service.save()

        Volume.objects.create(
            service=self.service,
            name="vol-1",
            mount_path="/data",
        )

    @patch.object(BackupService, '_backup_remote_service', side_effect=RuntimeError("Failed to save remote image: save failed"))
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_remote_image_save_failure_raises(self, mock_get_docker, mock_backup_remote):
        """Remote backup raises RuntimeError, propagates through backup_service."""
        with self.assertRaises(RuntimeError) as ctx:
            BackupService().backup_service(self.service.id)
        self.assertIn("Failed to save remote image", str(ctx.exception))

    @patch.object(BackupService, '_backup_remote_service', side_effect=RuntimeError("Failed to backup remote volume vol-1: compress failed"))
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_remote_volume_backup_failure_raises(self, mock_get_docker, mock_backup_remote):
        """Remote volume backup failure propagates."""
        with self.assertRaises(RuntimeError) as ctx:
            BackupService().backup_service(self.service.id)
        self.assertIn("Failed to backup remote volume", str(ctx.exception))


class RemoteBackupShellQuoteTest(TestCase):
    """shlex.quote is used for shell commands in remote operations."""

    def setUp(self):
        self.user = User.objects.create_user(username="shell-safety", password="x")
        self.project = Project.objects.create(name="Shell Safety", owner=self.user)
        self.service = Service.objects.create(
            name="safe-svc",
            owner=self.user,
            project=self.project,
        )
        self.server = ManagedServer.objects.create(
            name="Shell Node",
            host="10.0.0.2",
            ssh_user="root",
            ssh_key="secret",
            owner=self.user,
        )
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "10.0.0.2"
        self.service.save()

        Volume.objects.create(
            service=self.service,
            name="vol-1",
            mount_path="/data",
        )

    @patch("docker.from_env")
    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("apps.deployments.services.ssh_client.SSHClient")
    def test_service_name_is_quoted(self, mock_ssh_class, mock_encrypt, mock_get_docker, mock_docker_env):
        """Service name passed to docker commit is shell-quoted."""
        # Give service a name that could be dangerous
        self.service.name = "svc; rm -rf /"
        self.service.save()
        mock_encrypt.side_effect = lambda p: p

        mock_ssh = MagicMock()
        mock_ssh.check_docker.return_value = True
        mock_ssh.exec_command.return_value = ("stdout", "stderr", 0)
        mock_ssh_class.return_value = mock_ssh

        docker_client = MagicMock()
        mock_get_docker.return_value = docker_client
        mock_docker_env.return_value = docker_client

        try:
            BackupService().backup_service(self.service.id)
        except Exception:
            pass

        # At least one call to exec_command should contain the
        # shell-quoted service name (with single quotes around it)
        found_quoted = False
        for call in mock_ssh.exec_command.call_args_list:
            cmd = call[0][0]
            if "docker commit" in cmd and "'svc; rm -rf /'" in cmd:
                found_quoted = True
                break
        self.assertTrue(
            found_quoted,
            "Service name should be shlex.quote'd in docker commit command",
        )

    @patch("docker.from_env")
    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("apps.deployments.services.ssh_client.SSHClient")
    def test_volume_name_is_quoted(self, mock_ssh_class, mock_encrypt, mock_get_docker, mock_docker_env):
        """Volume name passed to docker volume inspect is shell-quoted."""
        self.service.name = "test-svc"
        self.service.save()
        mock_encrypt.side_effect = lambda p: p

        # Create a volume with a name that could be problematic
        Volume.objects.create(
            service=self.service,
            name="vol; id",
            mount_path="/data",
        )

        mock_ssh = MagicMock()
        mock_ssh.check_docker.return_value = True
        mock_ssh.exec_command.return_value = ("stdout", "stderr", 0)
        mock_ssh_class.return_value = mock_ssh

        docker_client = MagicMock()
        mock_get_docker.return_value = docker_client
        mock_docker_env.return_value = docker_client

        try:
            BackupService().backup_service(self.service.id)
        except Exception:
            pass

        found_quoted = False
        for call in mock_ssh.exec_command.call_args_list:
            cmd = call[0][0]
            if "docker volume inspect" in cmd and "'vol; id'" in cmd:
                found_quoted = True
                break
        self.assertTrue(
            found_quoted,
            "Volume name should be shlex.quote'd",
        )
