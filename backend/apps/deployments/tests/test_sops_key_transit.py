"""SOPS platform keypair transit (node decrypt provisioning)."""
from unittest.mock import patch

from django.test import TestCase

from apps.deployments.services.secrets_sops import store_platform_keypair

PUB = "age1ql5ks Ago public test recipient 0123456789abcdef"
PRIV = "AGE-SECRET-KEY-1testprivatekey0123456789abcdef0123456789ab"


class StorePlatformKeypairTests(TestCase):
    def test_rejects_bad_shapes(self):
        self.assertFalse(store_platform_keypair("", ""))
        self.assertFalse(store_platform_keypair("ssh-rsa AAAA", PRIV))
        self.assertFalse(store_platform_keypair(PUB, "password123"))
        self.assertFalse(store_platform_keypair("age1" + "x" * 200, PRIV))
        self.assertFalse(store_platform_keypair(PUB, "AGE-SECRET-KEY-" + "x" * 300))

    def test_stores_and_idempotent(self):
        self.assertTrue(store_platform_keypair(PUB, PRIV))
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.secrets_age_public_key, PUB)
        self.assertTrue(store_platform_keypair(PUB, PRIV) is False)

    def test_overwrites_stale_pair(self):
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        cfg.secrets_age_public_key = "age1stale00000000000000000000000000000000000000"
        cfg.save(update_fields=["secrets_age_public_key"])
        self.assertTrue(store_platform_keypair(PUB, PRIV))
        cfg.refresh_from_db()
        self.assertEqual(cfg.secrets_age_public_key, PUB)


class SopsBundleDistributionTests(TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch as _patch
        self._tmp = tempfile.mkdtemp(prefix="sops-dist-")
        self._patcher = _patch(
            "apps.deployments.services.secrets_sops.BUNDLE_DIR", self._tmp,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _enc(self, body="vars:\n  K: ENC[AES256_GCM,data:deadbeef,tag:abcd]\n"):
        return (
            "service: demo\nservice_id: demo\nvars:\n  K: ENC[AES256_GCM,data:deadbeef]\n"
            "sops:\n  age:\n    - recipient: age1test\n      enc: ENC[AGE]\n"
        )

    def test_store_and_read_round_trip(self):
        from django.contrib.auth import get_user_model
        from apps.deployments.models import Service
        from apps.deployments.services import secrets_sops
        user = get_user_model().objects.create_user(username="sops-dist-user", password="x")
        svc = Service.objects.create(name="sops-dist-svc", owner=user)
        import os
        self.assertIsNone(secrets_sops.read_service_bundle(svc))
        self.assertTrue(secrets_sops.store_service_bundle(svc, {"content": self._enc()}))
        path = os.path.join(self._tmp, f"{svc.id}.enc.yaml")
        if os.name == "posix":
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        bundle = secrets_sops.read_service_bundle(svc)
        self.assertIsNotNone(bundle)
        self.assertEqual(len(bundle["fingerprint"]), 16)
        self.assertIn("ENC[", bundle["content"])
        self.assertIn(self._enc(), bundle["content"])

    def test_store_rejects_shapes(self):
        from django.contrib.auth import get_user_model
        from apps.deployments.models import Service
        from apps.deployments.services import secrets_sops
        user = get_user_model().objects.create_user(username="sops-dist-user2", password="x")
        svc = Service.objects.create(name="sops-dist-svc2", owner=user)
        self.assertFalse(secrets_sops.store_service_bundle(svc, None))
        self.assertFalse(secrets_sops.store_service_bundle(svc, {"content": "plain yaml: yes"}))
        self.assertFalse(secrets_sops.store_service_bundle(svc, {"content": "x" * (256 * 1024 + 1)}))

    def test_trigger_ships_bundle_when_exported(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from apps.deployments.services.remote_orchestrator.deployment import DeploymentMixin
        orch = DeploymentMixin()
        orch._request = Mock(return_value=Mock(status_code=200))
        orch._parse_json_response = Mock(return_value={"id": "remote-1"})
        orch._set_last_error = Mock()
        deployment = SimpleNamespace(
            service=SimpleNamespace(name="svc", project=None),
            commit_hash="abc123",
        )
        bundle = {"fingerprint": "abcd1234abcd1234", "content": self._enc()}
        with patch(
            "apps.deployments.models.PlatformConfig.load",
            return_value=SimpleNamespace(server_ip="controller"),
        ), patch(
            "apps.deployments.services.infisical.resolve_service_token", return_value="",
        ), patch(
            "apps.deployments.services.secrets_sops.ensure_age_keypair",
            return_value=("age1test", "AGE-SECRET-KEY-1test"),
        ), patch(
            "apps.deployments.services.secrets_sops.read_service_bundle",
            return_value=bundle,
        ), patch(
            "apps.deployments.services.addon_mesh.rewrite_env_for_mesh", return_value={},
        ):
            rid = orch.trigger_deploy(deployment, "svc-remote-id")
        self.assertEqual(rid, "remote-1")
        _, kwargs = orch._request.call_args
        payload = kwargs.get("payload")
        self.assertEqual(payload["sops_bundle"], bundle)
