"""
Tests for ``verify_backup_integrity_task``.

The task checks backup archive integrity by validating file existence,
SHA-256 checksums (when present in metadata), and archive validity
(test-open with ``r:gz``).  Emits an ``AuditLog`` entry on each run.
"""
import hashlib
import io
import os
import tarfile
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.models_backup import ServerBackup, ServiceBackup
from apps.deployments.tasks_backup import verify_backup_integrity_task

User = get_user_model()


def _make_valid_tar(path: str, content: bytes = b"hello"):
    """Write a valid gzipped tar archive to *path*."""
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name="data.txt")
        info.size = len(content)
        info.type = tarfile.REGTYPE
        tar.addfile(info, io.BytesIO(content))
    return path


class VerifyBackupIntegrityTaskTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="integrity", password="x")
        self.project = Project.objects.create(name="Integrity", owner=self.user)
        self.service = Service.objects.create(
            name="integrity-svc",
            owner=self.user,
            project=self.project,
        )

    def test_passes_valid_backup(self):
        """A valid tar.gz file with matching checksum passes."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        _make_valid_tar(tmp.name)
        tmp.close()

        sha = hashlib.sha256()
        with open(tmp.name, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={"checksum_sha256": sha.hexdigest()},
        )

        try:
            result = verify_backup_integrity_task(backup_ids=[backup.id])
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["passed"], 1)
            self.assertEqual(result["failed"], 0)
        finally:
            os.remove(tmp.name)

    def test_fails_on_missing_file(self):
        """Backup with a missing file_path fails the check."""
        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path="/nonexistent/path.tar.gz",
            metadata={},
        )
        result = verify_backup_integrity_task(backup_ids=[backup.id])
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["failed"], 1)

    def test_fails_on_checksum_mismatch(self):
        """Backup with a wrong checksum fails."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        _make_valid_tar(tmp.name)
        tmp.close()

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={"checksum_sha256": "0" * 64},  # definitely wrong
        )

        try:
            result = verify_backup_integrity_task(backup_ids=[backup.id])
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["passed"], 0)
            self.assertEqual(result["failed"], 1)
        finally:
            os.remove(tmp.name)

    def test_fails_on_empty_archive(self):
        """A valid gzipped tar with zero members should fail (empty archive)."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        # Write a valid but empty tar (no members)
        with tarfile.open(tmp.name, "w:gz"):
            pass
        tmp.close()

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={},
        )

        try:
            result = verify_backup_integrity_task(backup_ids=[backup.id])
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["passed"], 0)
            self.assertEqual(result["failed"], 1)
        finally:
            os.remove(tmp.name)

    def test_fails_on_invalid_archive(self):
        """Corrupt bytes are not a valid tar.gz — should fail."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        tmp.write(b"this is not a valid tar.gz archive")
        tmp.close()

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={},
        )

        try:
            result = verify_backup_integrity_task(backup_ids=[backup.id])
            self.assertEqual(result["checked"], 1)
            self.assertEqual(result["passed"], 0)
            self.assertEqual(result["failed"], 1)
        finally:
            os.remove(tmp.name)

    def test_no_candidates_returns_zero_counts(self):
        """No COMPLETED backups in the DB returns all zeros."""
        result = verify_backup_integrity_task()
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["failed"], 0)

    def test_sampling_works(self):
        """When no backup_ids given, samples recent COMPLETED backups."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        _make_valid_tar(tmp.name)
        tmp.close()

        ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={},
        )

        try:
            result = verify_backup_integrity_task(sample_size=5)
            self.assertGreaterEqual(result["checked"], 1)
            self.assertGreaterEqual(result["passed"], 1)
        finally:
            os.remove(tmp.name)

    def test_audit_log_written(self):
        """Verify run creates a BACKUP_INTEGRITY_CHECK audit entry."""
        from apps.deployments.models_audit import AuditLog

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        _make_valid_tar(tmp.name)
        tmp.close()

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path=tmp.name,
            metadata={},
        )

        try:
            verify_backup_integrity_task(backup_ids=[backup.id])
            audit = AuditLog.objects.filter(action="BACKUP_INTEGRITY_CHECK").first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.actor, "system")
            self.assertEqual(audit.metadata["checked"], 1)
            self.assertEqual(audit.metadata["passed"], 1)
            self.assertEqual(audit.metadata["failed"], 0)
        finally:
            os.remove(tmp.name)

    def test_server_backup_checked(self):
        """ServerBackup is also checked by the task."""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        _make_valid_tar(tmp.name)
        tmp.close()

        backup = ServerBackup.objects.create(
            status="COMPLETED",
            file_path=tmp.name,
        )

        try:
            result = verify_backup_integrity_task(backup_ids=[backup.id])
            self.assertEqual(result["passed"], 1)
        finally:
            os.remove(tmp.name)
