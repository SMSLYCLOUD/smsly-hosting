"""
Tests for ``purge_user_backups`` with JSON contains query on server backups.

The old implementation loaded all ServerBackups into Python and filtered
by service ID intersection.  The new implementation uses a PostgreSQL-native
JSON contains query (``Q(services_included__contains=[str(sid)])``) to
filter at the DB level.  This test verifies correctness when multiple
users' services are referenced by the same server backup.

The existing :mod:`test_backup_gdpr_cleanup` already covers the basic
purge path; this file covers edge cases specific to the JSON contains
approach.
"""
import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.models.backup import ServerBackup, ServiceBackup
from apps.deployments.services.backup_service import purge_user_backups

User = get_user_model()


class PurgeUserBackupsJSONQueryTest(TestCase):
    """GDPR purge with JSON contains query on ServerBackup.services_included."""

    def setUp(self):
        self.user1 = User.objects.create_user(username="gdpr-user1", password="x")
        self.user2 = User.objects.create_user(username="gdpr-user2", password="x")
        self.project1 = Project.objects.create(name="Proj1", owner=self.user1)
        self.project2 = Project.objects.create(name="Proj2", owner=self.user2)
        self.service1 = Service.objects.create(
            name="svc-1", owner=self.user1, project=self.project1,
        )
        self.service2 = Service.objects.create(
            name="svc-2", owner=self.user2, project=self.project2,
        )

    def _make_tarball(self) -> str:
        fd, path = tempfile.mkstemp(prefix="gdpr_json_", suffix=".tar.gz")
        os.close(fd)
        with open(path, "wb") as fh:
            fh.write(b"tarball-bytes")
        return path

    def test_purge_only_removes_server_backups_for_user_services(self):
        """A ServerBackup referencing services from two users should only
        be counted for the user whose services match, but the file is
        deleted once (not double-counted)."""
        path = self._make_tarball()
        try:
            # Server backup that covers BOTH user1's and user2's services
            ServerBackup.objects.create(
                status="COMPLETED",
                file_path=path,
                services_included=[str(self.service1.id), str(self.service2.id)],
            )

            # Purge user1
            counters = purge_user_backups(self.user1.id)
            self.assertGreaterEqual(counters.get("server_backups_deleted", 0), 1)
            self.assertGreaterEqual(counters.get("server_backup_files_deleted", 0), 1)

            # Purge user2 — the backup is already deleted, but the query
            # should still find the row and attempt cleanup (row should
            # be gone, so it's a no-op).
            counters2 = purge_user_backups(self.user2.id)
            # Since row was already deleted, server_backups_deleted should be 0
            self.assertEqual(counters2.get("server_backups_deleted", 0), 0)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_purge_server_backup_no_overlap(self):
        """A ServerBackup referencing only another user's services is not
        touched when purging this user."""
        path = self._make_tarball()
        try:
            ServerBackup.objects.create(
                status="COMPLETED",
                file_path=path,
                services_included=[str(self.service2.id)],  # only user2's service
            )

            counters = purge_user_backups(self.user1.id)
            self.assertEqual(counters.get("server_backups_deleted", 0), 0)
            self.assertEqual(counters.get("server_backup_files_deleted", 0), 0)

            # Still exists for user2
            self.assertTrue(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_purge_server_backup_multiple_user_services(self):
        """User owns multiple services; a server backup referencing any
        matching service is found by the JSON contains query."""
        service3 = Service.objects.create(
            name="svc-3", owner=self.user1, project=self.project1,
        )
        path = self._make_tarball()
        try:
            ServerBackup.objects.create(
                status="COMPLETED",
                file_path=path,
                services_included=[str(service3.id), str(self.service2.id)],
            )

            counters = purge_user_backups(self.user1.id)
            self.assertGreaterEqual(counters.get("server_backups_deleted", 0), 1)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_purge_handles_empty_services_included(self):
        """ServerBackup with empty services_included list is not matched."""
        path = self._make_tarball()
        try:
            ServerBackup.objects.create(
                status="COMPLETED",
                file_path=path,
                services_included=[],  # empty list
            )

            counters = purge_user_backups(self.user1.id)
            self.assertEqual(counters.get("server_backups_deleted", 0), 0)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_purge_service_backups_still_works(self):
        """ServiceBackup deletion (owner-based FK) is unchanged."""
        path = self._make_tarball()
        try:
            ServiceBackup.objects.create(
                service=self.service1, status="COMPLETED", file_path=path,
            )
            counters = purge_user_backups(self.user1.id)
            self.assertEqual(counters.get("service_backups_deleted", 0), 1)
            self.assertEqual(counters.get("service_backup_files_deleted", 0), 1)
        finally:
            if os.path.exists(path):
                os.remove(path)
