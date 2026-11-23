"""Tests for immediate pre-cryptd data-leak fixes."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.models import ServerBackup
from apps.deployments.models import (
    Deployment,
    EnvironmentVariable,
    Project,
    Service,
)
from apps.deployments.models.registry_scope import ScopedRegistry
from apps.deployments.serializers.deployment import DeploymentSerializer
from apps.deployments.services.task_encryption import (
    TaskEncryptionError,
    decrypt_arg,
    encrypt_arg,
)
from apps.deployments.utils.deployment import get_default_env_value

User = get_user_model()

TEST_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


class DefaultEnvNoLeakTests(TestCase):
    def test_never_leaks_control_plane_os_environ(self):
        secret_env = {
            "DATABASE_URL": "postgresql://paas_admin:SUPERSECRET@pgcat:6432/smsly",
            "REDIS_URL": "redis://:REDISSECRET@redis-primary:6379/0",
            "CELERY_BROKER_URL": "amqp://celery:BROKERSECRET@rabbitmq:5672//",
            "CELERY_RESULT_BACKEND": "redis://:RESULTSECRET@redis-primary:6379/2",
            "MONGODB_URI": "mongodb://root:MONGOSECRET@mongo:27017/admin",
        }
        with patch.dict(os.environ, secret_env, clear=False):
            for key in (
                "DATABASE_URL",
                "DB_URL",
                "DB_URI",
                "SQLALCHEMY_DATABASE_URI",
                "SQLALCHEMY_DATABASE_URL",
                "REDIS_URL",
                "CELERY_BROKER_URL",
                "BROKER_URL",
                "CELERY_RESULT_BACKEND",
                "RESULT_BACKEND",
                "MONGODB_URI",
                "MONGO_URI",
                "MONGO_URL",
            ):
                val, should_inject = get_default_env_value(key, {"dependencies": ["asyncpg"]}, "tenant-app")
                self.assertFalse(should_inject, f"{key} should not auto-inject")
                self.assertIsNone(val, f"{key} must return None instead of control-plane env")


@override_settings(CACHES=TEST_CACHES)
class RegistryOverrideMaskAndResolveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="regleakuser", email="regleak@test.com", password="pass1234"
        )
        self.project = Project.objects.create(owner=self.user, name="EphemRegProj")
        self.service = Service.objects.create(
            name="reg-leak-svc", owner=self.user, project=self.project
        )

    def test_serializer_masks_legacy_registry_override_secrets(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="deadbeef",
            status=Deployment.Status.QUEUED,
            registry_override={
                "url": "registry.example.com:5000",
                "username": "deployer",
                "password": "plaintext-legacy-password",
                "token": "plaintext-legacy-token",
            },
        )
        data = DeploymentSerializer(dep).data
        self.assertEqual(data["registry_override"]["url"], "registry.example.com:5000")
        self.assertEqual(data["registry_override"]["username"], "deployer")
        self.assertEqual(data["registry_override"]["password"], "********")
        self.assertEqual(data["registry_override"]["token"], "********")

    def test_pipeline_resolves_scoped_registry_password_when_override_omits_password(self):
        from django.contrib.contenttypes.models import ContentType
        from apps.deployments.services.pipeline.registry import RegistryMixin

        ct = ContentType.objects.get_for_model(Project)
        ScopedRegistry.objects.create(
            content_type=ct,
            object_id=self.project.id,
            registry_url="registry.internal:5000",
            username="robot$ci",
            password="scoped-secret-password",
        )
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="deadbeef",
            status=Deployment.Status.QUEUED,
            registry_override={
                "url": "registry.internal:5000",
                "username": "robot$ci",
            },
        )

        class DummyPipeline(RegistryMixin):
            def __init__(self, svc, d):
                self.service = svc
                self.deployment = d

        creds = DummyPipeline(self.service, dep)._resolve_push_credentials()
        self.assertEqual(creds["url"], "registry.internal:5000")
        self.assertEqual(creds["username"], "robot$ci")
        self.assertEqual(creds["password"], "scoped-secret-password")


@override_settings(CACHES=TEST_CACHES)
class EnvVarListMaskAndPatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="envleakuser", email="envleak@test.com", password="pass1234"
        )
        self.service = Service.objects.create(name="env-leak-svc", owner=self.user)
        self.secret_var = EnvironmentVariable.objects.create(
            service=self.service,
            key="API_SECRET",
            value="top-secret-value-123",
            is_secret=True,
        )
        self.public_var = EnvironmentVariable.objects.create(
            service=self.service,
            key="APP_ENV",
            value="production",
            is_secret=False,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_masks_secrets_for_session_users_and_detail_reveals(self):
        list_resp = self.client.get(f"/api/v1/services/{self.service.id}/env_vars/")
        self.assertEqual(list_resp.status_code, 200)
        by_key = {item["key"]: item["value"] for item in list_resp.data}
        self.assertEqual(by_key["API_SECRET"], "********")
        self.assertEqual(by_key["APP_ENV"], "production")

        detail_resp = self.client.get(
            f"/api/v1/services/{self.service.id}/env_vars/{self.secret_var.id}/"
        )
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.data["value"], "top-secret-value-123")

    def test_patch_env_var_does_not_raise_unbound_and_masks_secret(self):
        patch_resp = self.client.patch(
            f"/api/v1/services/{self.service.id}/env_vars/{self.secret_var.id}/",
            {"is_locked": True},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertTrue(patch_resp.data["is_locked"])
        self.assertEqual(patch_resp.data["value"], "********")


class ServerBackupEncryptionTests(TestCase):
    def test_backup_server_encrypts_tarball_before_upload(self):
        import json
        from cryptography.fernet import Fernet
        from apps.cloud.services.backup_service.core import BackupService

        svc = BackupService()
        svc._audit_log = MagicMock()
        uploaded_paths = []
        backup_key = Fernet.generate_key().decode()

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "BACKUP_ENCRYPTION_KEY": backup_key,
                "SMSLY_CRYPTD_URL": "http://127.0.0.1:9999",
                "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            },
            clear=False,
        ):
            svc.backup_dir = tmpdir
            svc._get_backups_dir = MagicMock(return_value=tmpdir)
            svc.docker_client = None

            with patch(
                "apps.cloud.services.backup_service.core._upload_backup_to_cloud",
                side_effect=lambda b, p, kind: uploaded_paths.append(p) or {"uploaded": True},
            ):
                backup = svc.backup_server(db_only=True, backup_type="SERVER")

            self.assertEqual(backup.status, "COMPLETED")
            self.assertTrue(backup.file_path.endswith(".enc"))
            self.assertTrue(os.path.exists(backup.file_path))
            self.assertEqual(uploaded_paths, [backup.file_path])
            self.assertIsNotNone(BackupService.get_encryption_header(backup.file_path))
            self.assertTrue(os.path.exists(f"{backup.file_path}.meta"))
            with open(f"{backup.file_path}.meta", "r", encoding="utf-8") as mf:
                smeta = json.load(mf)
            self.assertNotIn("cryptd_wrapped_dek", smeta)
            # Verify plaintext tarball was removed after encryption
            self.assertFalse(os.path.exists(backup.file_path[:-4]))


class TaskEncryptionStrictTests(TestCase):
    def test_strict_mode_rejects_invalid_key_instead_of_plaintext_fallback(self):
        with patch.dict(
            os.environ,
            {
                "SMSLY_TASK_ENCRYPTION_STRICT": "true",
                "SMSLY_TASK_ENCRYPTION_KEY": "not-a-valid-fernet-key",
            },
            clear=False,
        ):
            with self.assertRaises(TaskEncryptionError):
                encrypt_arg("db-root-password")

    def test_valid_fernet_roundtrip(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        with patch.dict(
            os.environ,
            {
                "SMSLY_TASK_ENCRYPTION_STRICT": "true",
                "SMSLY_TASK_ENCRYPTION_KEY": key,
            },
            clear=False,
        ):
            token = encrypt_arg("db-root-password")
            self.assertTrue(token.startswith("enc:"))
            self.assertEqual(decrypt_arg(token), "db-root-password")
