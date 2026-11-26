"""Unit tests for F11 per-tenant volume encryption, key wrapping, and API redaction."""
from __future__ import annotations

import datetime
import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.addons.services import volume_encryption
from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.addons.tasks import crud as crud_tasks
from apps.addons.views.crud import AddonSerializer
from apps.deployments.models import Addon, Service

User = get_user_model()


class KeyWrappingAndPreflightTests(SimpleTestCase):
    def test_hkdf_fernet_roundtrip_and_invalid_token(self):
        with mock.patch.dict(os.environ, {"SMSLY_VOLUME_KEK": "unit-test-master-secret"}):
            raw_dek, wrapped, key_id = volume_encryption.generate_wrapped_dek()
            self.assertEqual(len(raw_dek), 32)
            self.assertEqual(len(key_id), 12)
            self.assertEqual(volume_encryption.unwrap_dek(wrapped), raw_dek)
            with self.assertRaises(volume_encryption.VolumeEncryptionError):
                volume_encryption.unwrap_dek("invalid-token")

    def test_preferred_fallback_vs_required_fail_closed(self):
        addon = SimpleNamespace(provider_metadata={})
        with mock.patch.dict(os.environ, {"SMSLY_VOLUME_ENCRYPTION_POLICY": "preferred"}), \
             mock.patch.object(
                 volume_encryption,
                 "preflight",
                 return_value={"available": False, "backend": None, "reason": "no loop device", "capabilities": {}},
             ):
            res = volume_encryption.prepare_volume(addon, "vol-1")
        self.assertFalse(res["encrypted"])
        self.assertEqual(res["status"], "fallback_unencrypted")
        self.assertIn("no loop device", res["reason"])

        with mock.patch.dict(os.environ, {"SMSLY_VOLUME_ENCRYPTION_POLICY": "required"}), \
             mock.patch.object(
                 volume_encryption,
                 "preflight",
                 return_value={"available": False, "backend": None, "reason": "no loop device", "capabilities": {}},
             ):
            with self.assertRaises(volume_encryption.VolumeEncryptionError):
                volume_encryption.prepare_volume(addon, "vol-2")

    def test_prepare_volume_daemon_backend_stores_wrapped_dek(self):
        addon = SimpleNamespace(provider_metadata={})
        with mock.patch.dict(
            os.environ,
            {
                "SMSLY_VOLUME_ENCRYPTION_POLICY": "required",
                "SMSLY_VOLUME_KEK": "test-kek-value",
                "SMSLY_VOLUME_DAEMON_URL": "http://127.0.0.1:8972",
            },
        ), mock.patch.object(
            volume_encryption,
            "preflight",
            return_value={"available": True, "backend": "daemon", "reason": "", "capabilities": {}},
        ), mock.patch.object(
            volume_encryption, "_docker_volume_exists", return_value=False
        ), mock.patch.object(
            volume_encryption,
            "_daemon_request",
            return_value=(200, {"ok": True, "host_path": "/var/lib/smsly/encrypted-volumes/mnt/v/data"}),
        ) as mreq:
            res = volume_encryption.prepare_volume(addon, "smsly-addon-postgres-1-data")

        self.assertTrue(res["encrypted"])
        self.assertEqual(res["backend"], "daemon")
        self.assertEqual(res["status"], "encrypted_active")
        self.assertTrue(res["wrapped_dek"])
        self.assertTrue(res["key_id"])
        self.assertEqual(mreq.call_args[0][0], "/volumes/provision")
        sanitized = volume_encryption.get_sanitized_status(addon)
        self.assertTrue(sanitized["encrypted"])
        self.assertNotIn("wrapped_dek", sanitized)

    def test_verify_or_unlock_fails_closed_when_locked(self):
        with mock.patch.dict(os.environ, {"SMSLY_VOLUME_KEK": "test-kek-value"}):
            _, wrapped, key_id = volume_encryption.generate_wrapped_dek()
            addon = SimpleNamespace(
                provider_metadata={
                    "volume_encryption": {
                        "encrypted": True,
                        "backend": "luks",
                        "wrapped_dek": wrapped,
                        "key_id": key_id,
                    }
                }
            )
            with mock.patch.object(
                volume_encryption,
                "_run_crypt_script",
                side_effect=volume_encryption.VolumeEncryptionError("unlock failed"),
            ):
                with self.assertRaises(volume_encryption.VolumeEncryptionError):
                    volume_encryption.verify_or_unlock(addon, "vol-locked")

    def test_validate_volume_name_enforces_regex_and_rejects_dotdot(self):
        self.assertEqual(volume_encryption.validate_volume_name("vol_1.data-a"), "vol_1.data-a")
        self.assertEqual(volume_encryption.validate_volume_name("a"), "a")
        for bad in ("", ".hidden", "-leading-dash", "vol..bad", "a/b", "vol name", "x" * 128):
            with self.assertRaises(volume_encryption.VolumeEncryptionError):
                volume_encryption.validate_volume_name(bad)

    def test_verify_or_unlock_persists_updated_wrapped_dek_week_and_key_id(self):
        addon = SimpleNamespace(
            provider_metadata={
                "volume_encryption": {
                    "encrypted": True,
                    "backend": "daemon",
                    "wrapped_dek": "grid:v1:10:svc-1:k1:oldblob",
                    "key_id": "k1",
                    "week": 10,
                }
            },
            service=SimpleNamespace(id="svc-1", project_id="proj-1"),
        )
        with mock.patch.dict(
            os.environ,
            {"SMSLY_VOLUME_DAEMON_URL": "http://127.0.0.1:9105"},
        ), mock.patch.object(
            volume_encryption,
            "_daemon_request",
            return_value=(
                200,
                {
                    "ok": True,
                    "wrapped_dek": "grid:v1:12:svc-1:k2:newblob",
                    "week": 12,
                    "key_id": "k2",
                    "host_path": "/var/lib/smsly/encrypted-volumes/mnt/vol-1/data",
                    "cipher": "aes-256-xts-luks2",
                },
            ),
        ):
            ve = volume_encryption.verify_or_unlock(addon, "vol-1")

        self.assertEqual(ve["wrapped_dek"], "grid:v1:12:svc-1:k2:newblob")
        self.assertEqual(ve["week"], 12)
        self.assertEqual(ve["key_id"], "k2")
        self.assertEqual(addon.provider_metadata["volume_encryption"]["wrapped_dek"], "grid:v1:12:svc-1:k2:newblob")
        self.assertEqual(addon.provider_metadata["volume_encryption"]["week"], 12)


class AddonEncryptionIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="volenc", password="x")
        self.service = Service.objects.create(name="volencsvc", owner=self.user)

    def test_serializer_exposes_sanitized_status_without_wrapped_dek(self):
        addon = Addon.objects.create(
            service=self.service,
            name="pg-enc",
            addon_type="POSTGRES",
            status=Addon.Status.ACTIVE,
            provider_metadata={
                "volume_encryption": {
                    "encrypted": True,
                    "backend": "luks",
                    "policy": "required",
                    "status": "encrypted_active",
                    "key_id": "abc123def456",
                    "wrapped_dek": "SECRET-FERNET-TOKEN",
                    "cipher": "aes-256-xts-luks2",
                }
            },
        )
        data = AddonSerializer(addon).data
        self.assertIn("volume_encryption", data)
        self.assertTrue(data["volume_encryption"]["encrypted"])
        self.assertEqual(data["volume_encryption"]["key_id"], "abc123def456")
        self.assertNotIn("wrapped_dek", data["volume_encryption"])

    def test_purge_retired_volumes_cleans_encrypted_and_tls_volumes(self):
        addon = Addon.objects.create(
            service=self.service,
            name="pg-purge",
            addon_type="POSTGRES",
            status=Addon.Status.DELETED,
            provision_mode="container",
            connection_url="postgresql://u:p@h:5432/d",
            provider_metadata={
                "volume_encryption": {
                    "encrypted": True,
                    "backend": "luks",
                    "key_id": "k1",
                }
            },
        )
        addon.deleted_at = timezone.now() - datetime.timedelta(days=20)
        addon.retired_volume = "smsly-addon-postgres-test-data"
        addon.save()

        proc = mock.MagicMock(returncode=0, stderr="")
        with mock.patch("subprocess.run", return_value=proc) as mrun, \
             mock.patch("apps.addons.services.volume_encryption.cleanup_volume", return_value=True) as mclean:
            crud_tasks.purge_retired_addon_volumes_task.run()

        mclean.assert_called_once()
        cmds = [" ".join(c.args[0]) for c in mrun.call_args_list]
        self.assertTrue(any("smsly-addon-postgres-test-data" in c for c in cmds))
        self.assertTrue(any("smsly-addon-postgres-test-tls" in c for c in cmds))

    def test_provisioner_calls_ensure_addon_data_volume(self):
        addon = Addon.objects.create(
            service=self.service,
            name="rd-enc",
            addon_type="REDIS",
            status=Addon.Status.ACTIVE,
        )
        prov = AddonProvisioner()
        with mock.patch.object(prov, "_ensure_network"), \
             mock.patch.object(prov, "_container_status", return_value=(None, False)), \
             mock.patch.object(prov, "_ensure_addon_data_volume") as mensure, \
             mock.patch.object(prov, "_provision_redis", return_value=("cid1", "rediss://:pw@rd-enc:6379/0?ssl_cert_reqs=none")), \
             mock.patch.object(prov, "_connect_addon_networks"):
            cid, url = prov.provision(addon)
        self.assertEqual(cid, "cid1")
        self.assertTrue(url.startswith("rediss://"))
        mensure.assert_called_once()
