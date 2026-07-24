"""Audit log coverage for domain and backup events (Fix 5)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.deployments.models import Project, Service
from apps.deployments.models.audit import AuditLog
from apps.deployments.models.backup import ServiceBackup

User = get_user_model()


FAST_THROTTLE_RATES = {
    "anon": "200/hour",
    "user": "5000/hour",
    "caddy_ask": "1000/min",
}

REST_FRAMEWORK_LOOSE = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.deployments.models.api_token.APITokenAuthentication",
        "apps.deployments.models.api_token.RemoteSyncHMACAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "apps.core.auth.CsrfExemptSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": FAST_THROTTLE_RATES,
}

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "audit-log-tests",
    }
}


@override_settings(CACHES=TEST_CACHES, REST_FRAMEWORK=REST_FRAMEWORK_LOOSE)
class AuditLogDomainBackupTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

        from rest_framework.test import APIClient

        self.user = User.objects.create_user(
            username="audit-owner", password="x",
        )
        self.project = Project.objects.create(
            name="Audit Proj", owner=self.user,
        )
        self.service = Service.objects.create(
            name="audit-service",
            owner=self.user,
            project=self.project,
            public_domain="audit.cloud.smsly.cloud",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _add_domain_url(self):
        return f"/api/v1/services/{self.service.id}/add-domain/"

    def _delete_domain_url(self):
        return f"/api/v1/services/{self.service.id}/delete-domain/"

    def _verify_domain_url(self):
        return f"/api/v1/services/{self.service.id}/verify-domain/"

    def test_add_domain_writes_audit_log(self):
        with patch("apps.deployments.views.ServiceViewSet._sync_caddy") as mock_sync, \
             patch("apps.deployments.views._normalize_request_domain",
                   side_effect=lambda v: (v, None)):
            mock_sync.return_value = {"ok": True, "message": ""}
            response = self.client.post(
                self._add_domain_url(),
                {"domain": "auditadd.example.com"},
                format="json",
            )
        self.assertIn(response.status_code, (200, 201))
        log = AuditLog.objects.filter(action="DOMAIN_ADD").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata["domain"], "auditadd.example.com")
        self.assertEqual(log.metadata["service_id"], str(self.service.id))

    def test_delete_domain_writes_audit_log(self):
        with patch("apps.deployments.views.ServiceViewSet._sync_caddy") as mock_sync, \
             patch("apps.deployments.views._normalize_request_domain",
                   side_effect=lambda v: (v, None)):
            mock_sync.return_value = {"ok": True, "message": ""}
            self.client.post(
                self._add_domain_url(),
                {"domain": "auditdel.example.com"},
                format="json",
            )
            response = self.client.post(
                self._delete_domain_url(),
                {"domain": "auditdel.example.com"},
                format="json",
            )
        self.assertIn(response.status_code, (200, 204))
        log = AuditLog.objects.filter(action="DOMAIN_DELETE").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata["domain"], "auditdel.example.com")

    def test_verify_domain_writes_audit_log(self):
        with patch("apps.deployments.views.ServiceViewSet._sync_caddy") as mock_sync, \
             patch("apps.deployments.views._normalize_request_domain",
                   side_effect=lambda v: (v, None)), \
             patch("apps.domains.verification.verify_custom_domain_dns") as mock_verify:
            mock_sync.return_value = {"ok": True, "message": ""}
            mock_verify.return_value = MagicMock(
                verified=True,
                expected="audit.cloud.smsly.cloud",
                actual="audit.cloud.smsly.cloud",
                error="",
            )
            response = self.client.post(
                self._verify_domain_url(),
                {"domain": "auditverify.example.com"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        log = AuditLog.objects.filter(action="DOMAIN_VERIFY").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata["domain"], "auditverify.example.com")
        self.assertEqual(log.metadata["result"], "success")

    def test_restore_service_backup_task_writes_audit_log(self):
        from apps.deployments.services.backup_service import BackupService
        from apps.deployments.tasks.data.tasks_backup import restore_service_backup_task

        backup = ServiceBackup.objects.create(
            service=self.service,
            status="COMPLETED",
            file_path="/nonexistent/test.tar.gz",
        )

        with patch.object(BackupService, "restore_service") as mock_restore:
            mock_restore.return_value = True
            restore_service_backup_task.run(
                str(backup.id), requesting_user_id=self.user.id,
            )
            mock_restore.assert_called_once()

        log = AuditLog.objects.filter(
            action="BACKUP_RESTORE",
            target=f"Backup: {backup.id}",
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata["scope"], "service")
        self.assertEqual(log.metadata["backup_id"], str(backup.id))
