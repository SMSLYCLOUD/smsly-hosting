"""Database regressions for rollback selection, review pauses and backup erasure."""
import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.models.backup import ServiceBackup, ServerBackup
from apps.cloud.services import backup_service
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.deploy.deletion import delete_service_task
from apps.deployments.tasks.recover_stalled_deployments import recover_stalled_deployments
from apps.deployments.views.service.deploy import DeployActionsMixin


class LifecycleReadinessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="lifecycle-owner")
        self.provider = CloudProvider.objects.create(name="lifecycle-local", provider_type="LOCAL", is_active=True)
        self.service = Service.objects.create(name="lifecycle-app", owner=self.user, provider=self.provider, branch="main")
        self.other = Service.objects.create(name="lifecycle-other", owner=self.user)

    def deployment(self, status, commit, finished=True):
        return Deployment.objects.create(service=self.service, status=status, commit_hash=commit,
                                         finished_at=timezone.now() if finished else None)

    def rollback(self):
        view = DeployActionsMixin()
        view.get_object = lambda: self.service
        request = SimpleNamespace(data={"confirm": True}, user=self.user)
        with patch("apps.deployments.views.service.deploy.smart_deploy_task.delay"):
            return view.instant_rollback(request)

    def test_rollback_selects_previous_inactive_success(self):
        prior = self.deployment("ACTIVE", "oldgood")
        current = self.deployment("ACTIVE", "current")
        response = self.rollback()
        self.assertEqual(response.status_code, 201)
        queued = Deployment.objects.get(is_rollback=True)
        self.assertEqual(queued.commit_hash, prior.commit_hash)
        self.assertEqual(queued.rollback_from_id, current.id)

    def test_rollback_without_prior_release_is_noop(self):
        self.deployment("ACTIVE", "current")
        response = self.rollback()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "ROLLBACK_NOOP")
        self.assertFalse(Deployment.objects.filter(is_rollback=True).exists())

    def test_rollback_after_failure_selects_active_release(self):
        good = self.deployment("ACTIVE", "good")
        failed = self.deployment("FAILED", "bad")
        self.assertEqual(self.rollback().status_code, 201)
        queued = Deployment.objects.get(is_rollback=True)
        self.assertEqual(queued.commit_hash, good.commit_hash)
        self.assertEqual(queued.rollback_from_id, failed.id)

    def test_rollback_ignores_superseded_unfinished_inactive(self):
        prior = self.deployment("ACTIVE", "good")
        self.deployment("INACTIVE", "neverran", finished=False)
        self.deployment("ACTIVE", "current")
        self.assertEqual(self.rollback().status_code, 201)
        self.assertEqual(Deployment.objects.get(is_rollback=True).commit_hash, prior.commit_hash)

    def test_sweeper_preserves_human_review_but_recovers_dead_dispatch(self):
        paused = self.deployment("REVIEW", "paused", finished=False)
        paused.review_summary = {"services": [{"name": "app"}]}
        paused.save(update_fields=["review_summary"])
        dispatched = self.deployment("REVIEW", "dispatch", finished=False)
        stale = timezone.now() - timedelta(days=3)
        Deployment.objects.filter(pk__in=[paused.pk, dispatched.pk]).update(created_at=stale, updated_at=stale)
        result = recover_stalled_deployments.run()
        paused.refresh_from_db()
        dispatched.refresh_from_db()
        self.assertEqual(paused.status, "REVIEW")
        self.assertEqual(dispatched.status, "CANCELLED")
        self.assertEqual(result["cancelled"], 1)

    def backup(self, service):
        handle, path = tempfile.mkstemp(prefix="lifecycle-backup-")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return ServiceBackup.objects.create(service=service, status="COMPLETED", file_path=path)

    def delete(self):
        with patch("apps.deployments.services.deletion_orchestrator.DeletionOrchestrator") as orchestrator, \
             patch("apps.deployments.services.traefik_manager.canary_file.remove_canary_file"), \
             patch("apps.deployments.utils.target.resolve_active_execution_target", return_value={"server_obj": None}):
            orchestrator.return_value.delete_service_resources.return_value = True
            return delete_service_task.run(str(self.service.pk))

    def test_service_deletion_purges_only_its_backup_files(self):
        own = self.backup(self.service)
        other = self.backup(self.other)
        server = ServerBackup.objects.create(services_included=[str(self.service.pk), str(self.other.pk)])
        self.delete()
        self.assertFalse(Service.objects.filter(pk=self.service.pk).exists())
        self.assertFalse(os.path.exists(own.file_path))
        self.assertTrue(os.path.exists(other.file_path))
        self.assertTrue(ServiceBackup.objects.filter(pk=other.pk).exists())
        self.assertTrue(ServerBackup.objects.filter(pk=server.pk).exists())

    def sidecar(self, backup):
        path = backup.file_path + ".meta"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"env": "test-secret"}')
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_service_purge_removes_local_metadata_sidecar(self):
        own = self.backup(self.service)
        sidecar = self.sidecar(own)
        backup_service.purge_service_backups(self.service.pk)
        self.assertFalse(os.path.exists(own.file_path))
        self.assertFalse(os.path.exists(sidecar))
        self.assertFalse(ServiceBackup.objects.filter(pk=own.pk).exists())

    def test_sidecar_cleanup_failure_retains_record_for_retry(self):
        own = self.backup(self.service)
        sidecar = self.sidecar(own)
        remove = os.remove

        def remove_unless_locked(path):
            if path == sidecar:
                raise PermissionError("sidecar locked")
            return remove(path)

        with patch("apps.cloud.services.backup_service.operations.os.remove", side_effect=remove_unless_locked):
            with self.assertRaises(PermissionError):
                backup_service.purge_service_backups(self.service.pk)
        self.assertTrue(os.path.exists(sidecar))
        self.assertTrue(ServiceBackup.objects.filter(pk=own.pk).exists())
        backup_service.purge_service_backups(self.service.pk)
        self.assertFalse(os.path.exists(sidecar))
        self.assertFalse(ServiceBackup.objects.filter(pk=own.pk).exists())

    def test_failed_backup_file_cleanup_keeps_service_and_backup_for_retry(self):
        own = self.backup(self.service)
        with patch("apps.cloud.services.backup_service.operations.os.remove", side_effect=PermissionError("locked")):
            with self.assertRaises(Exception):
                self.delete()
        self.assertTrue(Service.objects.filter(pk=self.service.pk).exists())
        self.assertTrue(ServiceBackup.objects.filter(pk=own.pk).exists())

    def test_missing_local_backup_is_safe_to_retry(self):
        own = self.backup(self.service)
        os.remove(own.file_path)
        self.delete()
        self.assertFalse(Service.objects.filter(pk=self.service.pk).exists())
        self.assertFalse(ServiceBackup.objects.filter(pk=own.pk).exists())

    def test_cloud_cleanup_failure_keeps_backup_record(self):
        own = self.backup(self.service)
        own.cloud_bucket = "test-bucket"
        own.cloud_key = "test-key"
        own.save(update_fields=["cloud_bucket", "cloud_key"])
        with patch("apps.cloud.services.backup_service.cloud.delete_cloud_backup_object", return_value=False):
            with self.assertRaises(Exception):
                self.delete()
        self.assertTrue(Service.objects.filter(pk=self.service.pk).exists())
        self.assertTrue(ServiceBackup.objects.filter(pk=own.pk).exists())

    def test_user_with_no_services_does_not_purge_server_archives(self):
        empty_user = get_user_model().objects.create_user(username="empty-lifecycle-owner")
        server = ServerBackup.objects.create(services_included=[str(self.other.pk)])
        backup_service.purge_user_backups(empty_user.pk)
        self.assertTrue(ServerBackup.objects.filter(pk=server.pk).exists())
