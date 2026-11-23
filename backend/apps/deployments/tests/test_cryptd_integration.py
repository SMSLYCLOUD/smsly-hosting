"""Integration & opt-in policy tests for grid-cryptd Django integration."""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.crypto_daemon import (
    cryptd_client,
    is_service_encryption_enabled,
)
from apps.core.tasks.cryptd import sync_cryptd_leases_task
from apps.deployments.models import Project, Service
from apps.deployments.services.task_encryption import decrypt_arg, encrypt_arg

User = get_user_model()

TEST_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}


class _MockCryptdHandler(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.calls.append(("GET", self.path, None))
        if self.headers.get("X-Cryptd-Secret") != "test-cryptd-secret":
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/v1/status":
            body = {"state": "ACTIVE", "current_week": 10, "opted_services": 1}
        elif self.path.startswith("/v1/services/") and self.path.endswith("/policy"):
            body = {"ok": True, "opted_in": True, "allowed": True}
        elif self.path == "/volumes/health":
            body = {"ok": True, "fscrypt": True, "cryptsetup": True}
        else:
            body = {"ok": True}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        self.calls.append(("POST", self.path, payload))
        if self.headers.get("X-Cryptd-Secret") != "test-cryptd-secret":
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/v1/transit/encrypt":
            pt = payload.get("plaintext_b64", "")
            svc = payload.get("service_id", "paas-control-plane")
            body = {"ok": True, "token": f"grid:v1:10:{svc}:k1:{pt}"}
        elif self.path == "/v1/transit/decrypt":
            tok = payload.get("token", "")
            b64_part = tok.rsplit(":", 1)[-1]
            body = {"ok": True, "plaintext_b64": b64_part}
        elif self.path == "/v1/backups/wrap-key":
            self._last_wrapped_b64 = payload.get("dek_b64", "")
            proj = payload.get("project_id", "default")
            body = {"ok": True, "wrapped_token": f"grid:v1:10:{payload['service_id']}:bak1:{proj}:{self._last_wrapped_b64}"}
        elif self.path == "/v1/backups/unwrap-key":
            tok = payload.get("wrapped_token", "")
            parts = tok.split(":")
            proj_tok = parts[5] if len(parts) >= 7 else ""
            if proj_tok and proj_tok != payload.get("project_id", ""):
                self.send_response(423)
                self.end_headers()
                return
            dek_b64 = parts[-1] if len(parts) >= 7 else base64.b64encode(b"k" * 32).decode("ascii")
            body = {"ok": True, "dek_b64": dek_b64}
        elif self.path == "/volumes/provision":
            body = {
                "ok": True,
                "host_path": f"/var/lib/smsly/encrypted-volumes/{payload['volume_name']}/data",
                "cipher": "fscrypt-v2",
                "wrapped_dek": f"grid:v1:10:{payload.get('service_id') or 'vol'}:k1:xyz",
                "key_id": "k1",
            }
        else:
            body = {"ok": True}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@override_settings(CACHES=TEST_CACHES)
class CryptdOptInAndAPITests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _MockCryptdHandler.calls = []
        cls.httpd = HTTPServer(("127.0.0.1", 0), _MockCryptdHandler)
        cls.daemon_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        super().tearDownClass()

    def setUp(self):
        _MockCryptdHandler.calls.clear()
        self.user = User.objects.create_user(
            username="cryptuser", email="crypt@test.com", password="pass1234"
        )
        self.project = Project.objects.create(owner=self.user, name="CryptProj")
        self.service = Service.objects.create(
            name="optin-app", owner=self.user, project=self.project
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_service_defaults_opted_out_until_user_enables(self):
        self.assertFalse(self.service.encryption_at_rest_enabled)
        self.assertFalse(is_service_encryption_enabled(self.service))

        # Disabled at platform level by default
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CRYPTD_ENABLED", None)
            os.environ.pop("SMSLY_CRYPTD_URL", None)
            self.assertFalse(cryptd_client.is_enabled())

    def test_user_can_opt_in_and_opt_out_and_lock_unlock_via_api(self):
        # Cannot lock when service has not opted in
        resp_err = self.client.post(f"/api/v1/services/{self.service.id}/encryption/lock/")
        self.assertEqual(resp_err.status_code, 400)

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            # Opt IN
            resp = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/",
                {"enabled": True},
                format="json",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.data["encryption_at_rest_enabled"])
            self.service.refresh_from_db()
            self.assertTrue(self.service.encryption_at_rest_enabled)

            reg_calls = [c for c in _MockCryptdHandler.calls if c[1] == "/v1/services/register"]
            self.assertEqual(len(reg_calls), 1)
            self.assertTrue(reg_calls[0][2]["opted_in"])

            # Lock & Unlock actions
            lock_resp = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/lock/",
                {"reason": "audit-lock"},
                format="json",
            )
            self.assertEqual(lock_resp.status_code, 200)
            unlock_resp = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/unlock/",
                {"ttl_seconds": 3600},
                format="json",
            )
            self.assertEqual(unlock_resp.status_code, 200)

            # Opt OUT ("user can choose to use it or not")
            optout = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/",
                {"enabled": False},
                format="json",
            )
            self.assertEqual(optout.status_code, 200)
            self.assertFalse(optout.data["encryption_at_rest_enabled"])
            self.assertFalse(_MockCryptdHandler.calls[-1][2]["opted_in"])

    def test_transit_task_encryption_and_periodic_lease_sync(self):
        self.service.encryption_at_rest_enabled = True
        self.service.status = Service.Status.ACTIVE
        self.service.save(update_fields=["encryption_at_rest_enabled", "status"])

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "SMSLY_TASK_ENCRYPTION_STRICT": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            token = encrypt_arg("pg-super-secret", service_id=str(self.service.id))
            self.assertTrue(token.startswith("enc:grid:v1:"))
            plain = decrypt_arg(token, service_id=str(self.service.id))
            self.assertEqual(plain, "pg-super-secret")

            summary = sync_cryptd_leases_task()
            self.assertEqual(summary["synced"], 1)
            self.assertEqual(summary["errors"], 0)

    def test_addon_connection_url_parsing_mcp_masking_and_patch_sync(self):
        from apps.deployments.models import Addon, EnvironmentVariable
        from apps.mcp.tools import get_service_env_vars

        # Addon connection URLs populate db_roles/db_names/redis_users for point-of-contact proxy
        Addon.objects.create(
            service=self.service,
            project=self.project,
            name="primary-pg",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgresql://tenant_role:pw123@127.0.0.1:5432/tenant_db",
        )
        Addon.objects.create(
            service=self.service,
            project=self.project,
            name="cache-redis",
            addon_type=Addon.Type.REDIS,
            status=Addon.Status.ACTIVE,
            connection_url="redis://redis_svc_user:pw123@127.0.0.1:6379/0",
        )

        # Even if is_secret=False, MCP must mask credentialed DB URLs
        EnvironmentVariable.objects.create(
            service=self.service,
            key="DATABASE_URL",
            value="postgresql://tenant_role:pw123@127.0.0.1:5432/tenant_db",
            is_secret=False,
        )
        mcp_vars = get_service_env_vars(str(self.service.id), user_id=str(self.user.id))
        by_key = {item["key"]: item for item in mcp_vars}
        self.assertEqual(by_key["DATABASE_URL"]["value"], "********")
        self.assertTrue(by_key["DATABASE_URL"]["is_secret"])

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            # PATCH /services/{id}/ with encryption_at_rest_enabled=True syncs Addon DB/Redis routes
            patch_resp = self.client.patch(
                f"/api/v1/services/{self.service.id}/",
                {"encryption_at_rest_enabled": True},
                format="json",
            )
            self.assertEqual(patch_resp.status_code, 200)
            reg_calls = [c for c in _MockCryptdHandler.calls if c[1] == "/v1/services/register"]
            self.assertEqual(len(reg_calls), 1)
            payload = reg_calls[0][2]
            self.assertTrue(payload["opted_in"])
            self.assertIn("tenant_role", payload["db_roles"])
            self.assertIn("tenant_db", payload["db_names"])
            self.assertIn("redis_svc_user", payload["redis_users"])
            self.assertEqual(payload["upstream_pg"], "127.0.0.1:5432")
            self.assertEqual(payload["upstream_redis"], "127.0.0.1:6379")

    def test_auto_enables_when_configured_without_platform_toggle_and_invalid_ttl_returns_400(self):
        from apps.deployments.models.addons import Addon
        from apps.addons.services import volume_encryption

        with patch.dict(
            os.environ,
            {"CRYPTD_ENABLED": "", "SMSLY_CRYPTD_URL": "", "CRYPTD_SHARED_SECRET": "test-cryptd-secret"},
            clear=False,
        ):
            self.assertFalse(cryptd_client.is_enabled())

        with patch.dict(
            os.environ,
            {"CRYPTD_ENABLED": "", "SMSLY_CRYPTD_URL": self.daemon_url, "CRYPTD_SHARED_SECRET": "test-cryptd-secret"},
            clear=False,
        ):
            # Configured daemon + shared secret activates automatically without a global UI toggle.
            self.assertTrue(cryptd_client.is_enabled())

            # CRYPTD_ENABLED=false acts as emergency override.
            with patch.dict(os.environ, {"CRYPTD_ENABLED": "false"}, clear=False):
                self.assertFalse(cryptd_client.is_enabled())

            # Non-service tasks (no service_id) and standalone/non-opted-in addons stay outside cryptd.
            plain_tok = encrypt_arg("rep-secret-val")
            self.assertFalse(plain_tok.startswith("enc:grid:"))
            addon_unopted = Addon.objects.create(
                service=self.service, project=self.project, name="unopted-pg", addon_type=Addon.Type.POSTGRES
            )
            self.assertEqual(volume_encryption._daemon_url(addon_unopted), "")

            self.service.encryption_at_rest_enabled = True
            self.service.save(update_fields=["encryption_at_rest_enabled"])
            self.assertEqual(volume_encryption._daemon_url(addon_unopted), self.daemon_url)

            bad_ttl = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/unlock/",
                {"ttl_seconds": "not-an-int"},
                format="json",
            )
            self.assertEqual(bad_ttl.status_code, 400)
            neg_ttl = self.client.post(
                f"/api/v1/services/{self.service.id}/encryption/unlock/",
                {"ttl_seconds": -5},
                format="json",
            )
            self.assertEqual(neg_ttl.status_code, 400)

    def test_service_backup_wrap_unwrap_preserves_project_id_and_local_adapter_fails_closed(self):
        import tempfile
        from unittest.mock import MagicMock
        import docker.errors
        from cryptography.fernet import Fernet
        from apps.cloud.adapters.local import LocalAdapter
        from apps.cloud.services.backup_service.core import BackupService

        self.service.encryption_at_rest_enabled = True
        self.service.save(update_fields=["encryption_at_rest_enabled"])
        backup_key = Fernet.generate_key().decode()

        svc = BackupService()
        svc._audit_log = MagicMock()
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = docker.errors.NotFound("not running")
        svc.docker_client = mock_docker

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "BACKUP_ENCRYPTION_KEY": backup_key,
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, env, clear=False):
            svc.backup_dir = tmpdir
            svc._get_backups_dir = MagicMock(return_value=tmpdir)
            with patch(
                "apps.cloud.services.backup_service.core._upload_backup_to_cloud",
                return_value={"uploaded": False},
            ), patch(
                "apps.cloud.services.backup_service.core._dump_container_database"
            ), patch(
                "apps.cloud.services.backup_service.operations._dump_service_addons",
                return_value=[],
            ):
                backup = svc.backup_service(str(self.service.id), db_only=True)

            with open(backup.file_path + ".meta", "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            self.assertEqual(meta["cryptd_service_id"], str(self.service.id))
            self.assertEqual(meta["cryptd_project_id"], str(self.project.id))

            # Restore decrypt must unwrap using matching project_id (unwrap returns 423 on mismatch).
            dec_out = os.path.join(tmpdir, "restored.tar.gz")
            with patch.object(
                BackupService,
                "_make_private_decrypted_path",
                staticmethod(lambda suffix=".tar.gz": (dec_out, dec_out)),
            ):
                decrypted_path = BackupService.decrypt_backup(backup.file_path, backup_key)
                self.assertTrue(os.path.exists(decrypted_path))
                BackupService.cleanup_decrypted_path(decrypted_path)

            # Corrupting cryptd_project_id in .meta causes unwrap-key 423 -> decrypt_backup refuses restore.
            with open(backup.file_path + ".meta", "w", encoding="utf-8") as mf:
                bad_meta = dict(meta, cryptd_project_id="wrong-project")
                json.dump(bad_meta, mf)
            with self.assertRaisesRegex(ValueError, "Backup locked or refused"):
                BackupService.decrypt_backup(backup.file_path, backup_key)

            # LocalAdapter must fail closed instead of mounting unencrypted volume when opted-in provision fails.
            adapter = LocalAdapter.__new__(LocalAdapter)
            adapter.docker_client = MagicMock()
            adapter._resolve_network_name = MagicMock(return_value="smsly-net")
            with patch.object(
                cryptd_client,
                "provision_volume",
                side_effect=RuntimeError("vault locked"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Refusing unencrypted fallback"):
                    adapter.deploy_container(
                        service_name="optin-fail-closed-test",
                        image="nginx:alpine",
                        env_vars={},
                        cpu=1,
                        memory=512,
                        volumes=[{
                            "name": "vol-test-enc",
                            "mount_path": "/data",
                            "encrypted": True,
                            "service_id": str(self.service.id),
                            "project_id": str(self.project.id),
                        }],
                    )

            # restart_service must fail closed instead of restarting container when cryptd is locked
            from apps.deployments.services.power import restart_service
            with patch("apps.deployments.services.power._active_container_id", return_value="cid-abc"), \
                 patch(
                     "apps.core.crypto_daemon.register_and_unlock_service",
                     side_effect=RuntimeError("vault locked"),
                 ):
                res = restart_service(self.service, actor="test")
            self.assertFalse(res["ok"])
            self.assertIn("locked", res["error"])


@override_settings(CACHES=TEST_CACHES)
class CryptdRuntimeOverlayAndComposeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _MockCryptdHandler.calls = []
        cls.httpd = HTTPServer(("127.0.0.1", 0), _MockCryptdHandler)
        cls.daemon_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        super().tearDownClass()

    def setUp(self):
        _MockCryptdHandler.calls.clear()
        self.user = User.objects.create_user(
            username="cryptrtuser", email="cryptrt@test.com", password="pass1234"
        )
        self.project = Project.objects.create(owner=self.user, name="CryptRtProj")
        self.service = Service.objects.create(
            name="optin-rt-app", owner=self.user, project=self.project
        )

    def test_apply_cryptd_runtime_env_rewrites_derived_db_and_redis_vars(self):
        from apps.core.crypto_daemon import apply_cryptd_runtime_env, needs_host_gateway

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "CRYPTD_DB_GATE_ENABLED": "true",
            "CRYPTD_PG_GATE_ADDR": "host.docker.internal:15432",
            "CRYPTD_REDIS_GATE_ADDR": "host.docker.internal:16379",
        }
        runtime_env = {
            "DATABASE_URL": "postgresql://tenant:pw@smsly-pg:5432/appdb",
            "DB_HOST": "smsly-pg",
            "DB_PORT": "5432",
            "POSTGRES_HOST": "smsly-pg",
            "POSTGRES_PORT": "5432",
            "REDIS_URL": "redis://:rp@smsly-redis:6379/0",
            "CELERY_BROKER_URL": "redis://:rp@smsly-redis:6379/0",
            "CELERY_RESULT_BACKEND": "redis://:rp@smsly-redis:6379/1",
            "CACHE_URL": "redis://:rp@smsly-redis:6379/2",
        }
        with patch.dict(os.environ, env, clear=False):
            # Opted-out service stays completely untouched
            self.assertFalse(apply_cryptd_runtime_env(self.service, runtime_env))
            self.assertNotIn("GRID_CRYPTD_SERVICE_ID", runtime_env)
            self.assertEqual(runtime_env["DB_HOST"], "smsly-pg")

            # Opted-in service rewrites all DB/Redis URLs + derived host/port vars
            self.service.encryption_at_rest_enabled = True
            self.service.save(update_fields=["encryption_at_rest_enabled"])
            self.assertTrue(apply_cryptd_runtime_env(self.service, runtime_env))
            self.assertEqual(runtime_env["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
            self.assertEqual(runtime_env["DATABASE_URL"], "postgresql://tenant:pw@host.docker.internal:15432/appdb")
            self.assertEqual(runtime_env["DB_HOST"], "host.docker.internal")
            self.assertEqual(runtime_env["DB_PORT"], "15432")
            self.assertEqual(runtime_env["POSTGRES_HOST"], "host.docker.internal")
            self.assertEqual(runtime_env["POSTGRES_PORT"], "15432")
            self.assertEqual(runtime_env["REDIS_URL"], "redis://:rp@host.docker.internal:16379/0")
            self.assertEqual(runtime_env["CELERY_BROKER_URL"], "redis://:rp@host.docker.internal:16379/0")
            self.assertEqual(runtime_env["CELERY_RESULT_BACKEND"], "redis://:rp@host.docker.internal:16379/1")
            self.assertEqual(runtime_env["CACHE_URL"], "redis://:rp@host.docker.internal:16379/2")
            self.assertTrue(needs_host_gateway(runtime_env))

    def test_container_refresh_and_replica_preserve_cryptd_gate_and_host_gateway_on_runc(self):
        from unittest.mock import MagicMock
        import docker.errors
        from apps.autoscaler.models import ServiceReplica
        from apps.deployments.models import EnvironmentVariable
        from apps.deployments.services.container_refresh import (
            ContainerRefreshError,
            recreate_with_fresh_env,
        )
        from apps.deployments.services.spawning_service import SpawningService

        self.service.encryption_at_rest_enabled = True
        self.service.docker_image = "nginx:alpine"
        self.service.save(update_fields=["encryption_at_rest_enabled", "docker_image"])

        EnvironmentVariable.objects.create(
            service=self.service,
            key="DATABASE_URL",
            value="postgresql://tenant:pw@smsly-pg:5432/appdb",
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key="BROKEN_ENC",
            value="gAAAAAB" + ("A" * 80),
        )

        mock_client = MagicMock()
        mock_ctr = MagicMock()
        mock_ctr.id = "ctr1234567890"
        mock_ctr.name = self.service.name
        mock_ctr.status = "running"
        mock_ctr.image.tags = ["nginx:alpine"]
        mock_ctr.attrs = {
            "Config": {
                "Image": "nginx:alpine",
                "Labels": {},
                "Env": ["PORT=8000", "DB_HOST=smsly-pg", "DB_PORT=5432"],
            },
            "HostConfig": {
                "Runtime": "runc",
                "RestartPolicy": {"Name": "unless-stopped"},
            },
            "NetworkSettings": {
                "Networks": {"smsly-net": {"Aliases": [self.service.name]}}
            },
        }
        new_ctr = MagicMock()
        new_ctr.id = "new1234567890"
        new_ctr.status = "running"
        mock_client.containers.create.return_value = new_ctr

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "CRYPTD_DB_GATE_ENABLED": "true",
            "CRYPTD_PG_GATE_ADDR": "host.docker.internal:15432",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("apps.deployments.services.container_refresh._docker_client", return_value=mock_client), \
             patch("apps.deployments.services.container_refresh._resolve_target_container", return_value=mock_ctr):
            res = recreate_with_fresh_env(self.service)
            self.assertTrue(res["ok"])
            ckwargs = mock_client.containers.create.call_args.kwargs
            self.assertEqual(ckwargs["environment"]["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
            self.assertEqual(
                ckwargs["environment"]["DATABASE_URL"],
                "postgresql://tenant:pw@host.docker.internal:15432/appdb",
            )
            self.assertEqual(ckwargs["environment"]["DB_HOST"], "host.docker.internal")
            self.assertEqual(ckwargs["environment"]["DB_PORT"], "15432")
            self.assertNotIn("BROKEN_ENC", ckwargs["environment"])
            self.assertIn("host.docker.internal:host-gateway", ckwargs["extra_hosts"])

            # Fail closed on refresh BEFORE stopping old container when cryptd refuses
            mock_ctr.stop.reset_mock()
            with patch("apps.core.crypto_daemon.register_and_unlock_service", side_effect=RuntimeError("locked")):
                with self.assertRaises(ContainerRefreshError):
                    recreate_with_fresh_env(self.service)
                mock_ctr.stop.assert_not_called()

            # Spawning local replica on runc injects cryptd gate + host-gateway + drops gAAAA ciphertext
            replica = ServiceReplica.objects.create(
                service=self.service,
                container_name=f"{self.service.name}-rep-test",
            )
            mock_rep_ctr = MagicMock()
            mock_rep_ctr.id = "rep1234567890"
            mock_rep_client = MagicMock()
            mock_rep_client.containers.list.return_value = []
            mock_rep_client.containers.get.side_effect = docker.errors.NotFound("no ref")
            mock_rep_client.containers.run.return_value = mock_rep_ctr
            with patch("docker.from_env", return_value=mock_rep_client), \
                 patch("apps.deployments.services.container_runtime.get_runtime_for_container", return_value="runc"), \
                 patch("apps.deployments.services.spawning_service._scoped_network_for", return_value="smsly-net"), \
                 patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"):
                SpawningService().spawn_local(self.service, replica)
            rkwargs = mock_rep_client.containers.run.call_args.kwargs
            self.assertEqual(rkwargs["environment"]["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
            self.assertEqual(
                rkwargs["environment"]["DATABASE_URL"],
                "postgresql://tenant:pw@host.docker.internal:15432/appdb",
            )
            self.assertNotIn("BROKEN_ENC", rkwargs["environment"])
            self.assertIn("host.docker.internal:host-gateway", rkwargs["extra_hosts"])

    def test_compose_sanitizes_control_plane_env_and_injects_cryptd_override(self):
        import tempfile
        import yaml
        from types import SimpleNamespace
        from apps.deployments.services.pipeline.build import _safe_compose_process_env
        from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin

        secret_env = {
            "SECRET_KEY": "cp-secret-key",
            "FIELD_ENCRYPTION_KEY": "cp-fernet-key",
            "BACKUP_ENCRYPTION_KEY": "cp-bak-key",
            "CRYPTD_SHARED_SECRET": "cp-cryptd-secret",
            "DATABASE_URL": "postgresql://paas:secret@pgcat:6432/smsly",
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "PATH": "/usr/local/bin:/usr/bin",
        }
        with patch.dict(os.environ, secret_env, clear=False):
            safe_env = _safe_compose_process_env()
        self.assertEqual(safe_env["DOCKER_HOST"], "unix:///var/run/docker.sock")
        for leaked_key in ("SECRET_KEY", "FIELD_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY", "CRYPTD_SHARED_SECRET", "DATABASE_URL"):
            self.assertNotIn(leaked_key, safe_env)

        class DummyCompose(ComposeNetworkingMixin):
            def __init__(self, build_dir, svc):
                self.build_dir = build_dir
                self.source_dir = build_dir
                self.service = svc
                self.deployment = SimpleNamespace(id="dep-compose-1", staging_url=None)
                self.staged_only = False

            def _resolve_service_network_name(self):
                return "smsly-net"

            def _compose_traefik_labels(self, project_name):
                return {"traefik.enable": "true"}

        with tempfile.TemporaryDirectory() as tmpdir:
            self.service.compose_file = "docker-compose.yml"
            self.service.save(update_fields=["compose_file"])
            compose_path = os.path.join(tmpdir, "docker-compose.yml")
            with open(compose_path, "w", encoding="utf-8") as cf:
                cf.write("services:\n  web:\n    image: nginx:alpine\n")

            dc = DummyCompose(tmpdir, self.service)
            with patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runc"), \
                 patch("apps.deployments.services.container_runtime.is_sandboxed_runtime", return_value=False):
                override_path = dc._write_compose_routing_override(
                    "web",
                    "proj",
                    cryptd_env={"GRID_CRYPTD_SERVICE_ID": str(self.service.id)},
                    host_gateway=True,
                )
            with open(override_path, "r", encoding="utf-8") as ovf:
                ov = yaml.safe_load(ovf)
            web_ov = ov["services"]["web"]
            self.assertEqual(web_ov["environment"]["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
            self.assertIn("host.docker.internal:host-gateway", web_ov["extra_hosts"])

    def test_server_and_control_plane_backup_enforce_cryptd_unwrap_gate(self):
        import tempfile
        from unittest.mock import MagicMock
        import docker.errors
        from cryptography.fernet import Fernet
        from apps.cloud.services.backup_service.core import BackupService

        self.service.encryption_at_rest_enabled = True
        self.service.save(update_fields=["encryption_at_rest_enabled"])
        backup_key = Fernet.generate_key().decode()

        svc = BackupService()
        svc._audit_log = MagicMock()
        mock_docker = MagicMock()
        mock_docker.containers.get.side_effect = docker.errors.NotFound("not running")
        mock_docker.volumes.get.side_effect = docker.errors.NotFound("no volume")
        svc.docker_client = mock_docker

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "BACKUP_ENCRYPTION_KEY": backup_key,
            "CRYPTD_CONTROL_PLANE_BACKUP_ENCRYPT": "true",
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, env, clear=False):
            svc.backup_dir = tmpdir
            svc._get_backups_dir = MagicMock(return_value=tmpdir)
            with patch(
                "apps.cloud.services.backup_service.core._upload_backup_to_cloud",
                return_value={"uploaded": False},
            ):
                backup = svc.backup_server(db_only=True, backup_type="SERVER")

            with open(backup.file_path + ".meta", "r", encoding="utf-8") as mf:
                meta = json.load(mf)
            self.assertEqual(meta["cryptd_service_id"], cryptd_client.CONTROL_PLANE_SERVICE_ID)
            self.assertIn("cryptd_wrapped_dek", meta)

            # Corrupting control-plane project_id forces 423 -> decrypt_backup refuses (no bypass!)
            with open(backup.file_path + ".meta", "w", encoding="utf-8") as mf:
                json.dump(dict(meta, cryptd_project_id="tampered-proj"), mf)
            with self.assertRaisesRegex(ValueError, "Backup locked or refused"):
                BackupService.decrypt_backup(backup.file_path, backup_key)

    def test_compose_encrypted_volumes_replace_named_volumes_and_fail_closed(self):
        import tempfile
        import yaml
        from types import SimpleNamespace
        from apps.deployments.models import Volume
        from apps.deployments.services.pipeline.compose_networking import ComposeNetworkingMixin
        from apps.deployments.services.pipeline.exceptions import BuildError

        self.service.encryption_at_rest_enabled = True
        self.service.save(update_fields=["encryption_at_rest_enabled"])
        Volume.objects.create(service=self.service, name="svc-app-vol", mount_path="/app/uploads")

        class DummyComposeVol(ComposeNetworkingMixin):
            def __init__(self, build_dir, svc):
                self.build_dir = build_dir
                self.source_dir = build_dir
                self.service = svc
                self.deployment = SimpleNamespace(id="dep-compose-vol", staging_url=None)
                self.staged_only = False

            def _resolve_service_network_name(self):
                return "smsly-net"

            def _compose_traefik_labels(self, project_name):
                return {"traefik.enable": "true"}

        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, env, clear=False):
            self.service.compose_file = "docker-compose.yml"
            self.service.save(update_fields=["compose_file"])
            compose_path = os.path.join(tmpdir, "docker-compose.yml")
            with open(compose_path, "w", encoding="utf-8") as cf:
                cf.write(
                    "services:\n"
                    "  web:\n"
                    "    image: nginx:alpine\n"
                    "    volumes:\n"
                    "      - webdata:/var/lib/data\n"
                    "volumes:\n"
                    "  webdata:\n"
                )
            dc = DummyComposeVol(tmpdir, self.service)
            with patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runc"), \
                 patch("apps.deployments.services.container_runtime.is_sandboxed_runtime", return_value=False):
                ov_path = dc._write_compose_routing_override("web", "proj")
                dc._validate_merged_compose_config(compose_path, ov_path, {}, allowed_network="smsly-net")
                dc._inject_encrypted_compose_volumes(compose_path, ov_path, main_service="web")

            with open(ov_path, "r", encoding="utf-8") as ovf:
                ov = yaml.safe_load(ovf)
            vols = ov["services"]["web"]["volumes"]
            self.assertTrue(any(str(v).endswith(":/var/lib/data:rw") and "/var/lib/smsly/encrypted-volumes/" in v for v in vols))
            self.assertTrue(any(str(v) == "/var/lib/smsly/encrypted-volumes/svc-app-vol/data:/app/uploads:rw" for v in vols))

            # Refuses unencrypted fallback when cryptd fails to provision a compose volume
            with patch.object(cryptd_client, "provision_volume", side_effect=RuntimeError("vault locked")):
                with self.assertRaisesRegex(BuildError, "Refusing unencrypted fallback"):
                    dc._inject_encrypted_compose_volumes(compose_path, ov_path, main_service="web")

    def test_local_adapter_promote_container_preserves_host_gateway_even_on_runsc(self):
        from unittest.mock import MagicMock
        import docker.errors
        from apps.cloud.adapters.local import LocalAdapter

        adapter = LocalAdapter.__new__(LocalAdapter)
        mock_docker = MagicMock()
        adapter.docker_client = mock_docker
        adapter._resolve_network_name = MagicMock(return_value="smsly-net")
        adapter._apply_egress_restrictions = MagicMock()
        adapter._get_traefik_labels = MagicMock(return_value={})
        adapter._apply_router_special_labels = MagicMock()
        adapter._wait_container_healthy = MagicMock(return_value=True)
        adapter._cleanup_predecessor_container = MagicMock()
        adapter._is_running_as_root = MagicMock(return_value=True)

        green_ctr = MagicMock()
        green_ctr.id = "green123"
        green_ctr.name = f"{self.service.name}-green"
        green_ctr.labels = {"smsly.service": self.service.name}
        green_ctr.image.tags = ["nginx:alpine"]
        green_ctr.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Image": "nginx:alpine",
                "Env": [
                    f"GRID_CRYPTD_SERVICE_ID={self.service.id}",
                    "DATABASE_URL=postgresql://u:p@host.docker.internal:15432/db",
                ],
                "Labels": {"smsly.service": self.service.name},
            },
            "HostConfig": {
                "Runtime": "runsc",
                "ExtraHosts": ["pg-addon:172.18.0.5"],
                "RestartPolicy": {"Name": "unless-stopped"},
            },
            "NetworkSettings": {
                "Networks": {"smsly-net": {"Aliases": [green_ctr.name]}}
            },
        }
        promoted_ctr = MagicMock()
        promoted_ctr.id = "promoted123"
        promoted_ctr.status = "running"
        promoted_ctr.attrs = {"State": {"Status": "running", "Health": {"Status": "healthy"}}}

        def _get_ctr(cid):
            if cid == "green123":
                return green_ctr
            raise docker.errors.NotFound(cid)

        mock_docker.containers.get.side_effect = _get_ctr
        mock_docker.containers.create.return_value = promoted_ctr

        with patch("apps.deployments.services.container_refresh._resolve_gvisor_extra_hosts", return_value=["pg-addon:172.18.0.5"]), \
             patch("apps.deployments.services.container_runtime.is_sandboxed_runtime", return_value=False):
            adapter.promote_container(self.service.name, "green123")

        ckwargs = mock_docker.containers.create.call_args.kwargs
        self.assertIn("host.docker.internal:host-gateway", ckwargs["extra_hosts"])

    def test_remote_orchestrator_sync_trigger_and_remote_refresh_enforce_cryptd_mesh_gate(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from apps.deployments.models import EnvironmentVariable
        from apps.deployments.services.container_refresh import (
            ContainerRefreshError,
            _recreate_remote_with_fresh_env,
        )
        from apps.deployments.services.remote_orchestrator.deployment import DeploymentMixin
        from apps.deployments.services.remote_orchestrator.service_sync import ServiceSyncMixin

        self.service.encryption_at_rest_enabled = True
        self.service.save(update_fields=["encryption_at_rest_enabled"])
        EnvironmentVariable.objects.create(
            service=self.service,
            key="DATABASE_URL",
            value="postgresql://tenant:pw@smsly-pg:5432/appdb",
        )

        # 1. ServiceSyncMixin includes encryption_at_rest_enabled
        payload = ServiceSyncMixin()._service_sync_payload(self.service)
        self.assertTrue(payload["encryption_at_rest_enabled"])

        # 2. DeploymentMixin.trigger_deploy ships GRID_CRYPTD_SERVICE_ID + mesh DB gate URL
        class DummyRemote(DeploymentMixin):
            def __init__(self):
                self.server = SimpleNamespace(host="10.100.0.9")
                self.sent = None

            def _request(self, method, path, payload=None, timeout=None):
                self.sent = (method, path, payload)
                resp = MagicMock()
                resp.status_code = 200
                return resp

            def _parse_json_response(self, resp, ctx):
                return {"deployment_id": "rem-1"}

            def _set_last_error(self, msg, response=None):
                pass

        rem = DummyRemote()
        dep = SimpleNamespace(service=self.service, commit_hash="abc123")
        env = {
            "CRYPTD_ENABLED": "true",
            "SMSLY_CRYPTD_URL": self.daemon_url,
            "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
            "CRYPTD_DB_GATE_ENABLED": "true",
            "CRYPTD_MESH_PG_GATE_ADDR": "10.100.0.1:15432",
        }
        with patch.dict(os.environ, env, clear=False):
            rem_id = rem.trigger_deploy(dep, "remote-svc-1")
            self.assertEqual(rem_id, "rem-1")
            sent_payload = rem.sent[2]
            self.assertEqual(sent_payload["mesh_env"]["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
            self.assertEqual(
                sent_payload["mesh_env"]["DATABASE_URL"],
                "postgresql://tenant:pw@10.100.0.1:15432/appdb",
            )

            # 3. _recreate_remote_with_fresh_env sends GRID_CRYPTD_SERVICE_ID + fails closed on lock
            fake_server = SimpleNamespace(name="node-2", is_primary=False, host="10.100.0.9")
            mock_orch = MagicMock()
            mock_orch.recreate_remote_container.return_value = {"ok": True}
            with patch(
                "apps.deployments.services.caddy_manager.config_generation._resolve_effective_server",
                return_value=fake_server,
            ), patch(
                "apps.deployments.services.remote_orchestrator.RemoteOrchestrator",
                return_value=mock_orch,
            ):
                res = _recreate_remote_with_fresh_env(self.service)
                self.assertTrue(res["ok"])
                ov_env = mock_orch.recreate_remote_container.call_args[0][1]
                self.assertEqual(ov_env["GRID_CRYPTD_SERVICE_ID"], str(self.service.id))
                self.assertEqual(ov_env["DATABASE_URL"], "postgresql://tenant:pw@10.100.0.1:15432/appdb")

            with patch(
                "apps.deployments.services.caddy_manager.config_generation._resolve_effective_server",
                return_value=fake_server,
            ), patch(
                "apps.core.crypto_daemon.register_and_unlock_service",
                side_effect=RuntimeError("vault locked"),
            ):
                with self.assertRaises(ContainerRefreshError):
                    _recreate_remote_with_fresh_env(self.service)

    def test_backup_download_returns_423_when_locked_even_without_local_key(self):
        import tempfile
        from apps.cloud.models import ServiceBackup

        self.service.encryption_at_rest_enabled = True
        self.service.save(update_fields=["encryption_at_rest_enabled"])
        with tempfile.TemporaryDirectory() as tmpdir:
            enc_path = os.path.join(tmpdir, "svc-locked.tar.gz.enc")
            with open(enc_path, "wb") as f:
                f.write(b"cipherbytes")
            with open(enc_path + ".meta", "w", encoding="utf-8") as mf:
                json.dump({
                    "cryptd_wrapped_dek": f"grid:v1:10:{self.service.id}:bak1:wrong-proj:AAAA",
                    "cryptd_service_id": str(self.service.id),
                    "cryptd_project_id": "real-proj",
                }, mf)
            bak = ServiceBackup.objects.create(
                service=self.service,
                created_by=self.user,
                file_path=enc_path,
                status="COMPLETED",
            )
            env = {
                "CRYPTD_ENABLED": "true",
                "SMSLY_CRYPTD_URL": self.daemon_url,
                "CRYPTD_SHARED_SECRET": "test-cryptd-secret",
                "BACKUP_ENCRYPTION_KEY": "",
            }
            api = APIClient()
            api.force_authenticate(user=self.user)
            url_resp = api.get(f"/api/v1/backups/{bak.id}/download-url/")
            self.assertEqual(url_resp.status_code, 200)
            signed_url = url_resp.data["url"]
            signed_q = signed_url.split("?", 1)[-1]
            with patch.dict(os.environ, env, clear=False):
                resp = api.get(f"/api/v1/backups/{bak.id}/download/?{signed_q}")
            self.assertEqual(resp.status_code, 423)
