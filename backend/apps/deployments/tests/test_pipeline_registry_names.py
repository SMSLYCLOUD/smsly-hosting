"""Registry-qualified local image names (build-cache engagement).

Pure-function tests for the naming helpers plus a fixture-backed test
proving _push_image strips the local prefix before pushing elsewhere.
Docker is fully mocked; DB fixtures mirror test_remote_hardening.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.deployments.services.pipeline.build import (
    _local_registry_host,
    _with_local_registry,
)


@override_settings(CONTAINER_REGISTRY_URL="registry:5000")
class LocalRegistryNamingTests(TestCase):
    def test_bare_name_gets_qualified(self):
        self.assertEqual(
            _with_local_registry("smsly/myapp:abc1234", None),
            "registry:5000/smsly/myapp:abc1234",
        )

    def test_already_qualified_untouched(self):
        name = "registry:5000/smsly/myapp:abc1234"
        self.assertEqual(_with_local_registry(name, None), name)

    def test_external_name_untouched(self):
        for name in ("ghcr.io/org/img:v1", "docker.io/library/redis:7"):
            self.assertEqual(_with_local_registry(name, None), name)

    def test_kill_switch_restores_bare(self):
        with patch.dict(
            "os.environ", {"SMSLY_REGISTRY_IMAGE_NAMES": "0"}, clear=False,
        ):
            self.assertEqual(
                _with_local_registry("smsly/myapp:abc1234", None),
                "smsly/myapp:abc1234",
            )

    def test_non_local_deployment_falls_back_to_bare_off_mesh(self):
        # Mesh unreachable (mocked socket failure) → legacy bare names.
        dep = SimpleNamespace()
        with patch(
            "apps.deployments.utils.is_deployment_local", return_value=False,
        ), patch("socket.create_connection",
                 side_effect=OSError("no route")):
            self.assertEqual(
                _with_local_registry("smsly/myapp:abc1234", dep),
                "smsly/myapp:abc1234",
            )

    def test_non_local_deployment_uses_mesh_when_reachable(self):
        import apps.deployments.services.pipeline.build as build_mod
        dep = SimpleNamespace()
        # Reset the process cache so the probe actually runs.
        build_mod._MESH_OK_HOST = None
        try:
            with patch(
                "apps.deployments.utils.is_deployment_local",
                return_value=False,
            ), patch("socket.create_connection"), patch(
                "apps.deployments.services.provisioner.helpers."
                "server_config._get_master_mesh_ip",
                return_value="10.100.0.1",
            ):
                self.assertEqual(
                    _with_local_registry("smsly/myapp:abc1234", dep),
                    "10.100.0.1:5000/smsly/myapp:abc1234",
                )
        finally:
            build_mod._MESH_OK_HOST = None

    def test_local_deployment_gets_qualified(self):
        dep = SimpleNamespace()
        with patch(
            "apps.deployments.utils.is_deployment_local", return_value=True,
        ):
            self.assertEqual(
                _with_local_registry("smsly/myapp:abc1234", dep),
                "registry:5000/smsly/myapp:abc1234",
            )

    def test_empty_name_safe(self):
        self.assertEqual(_with_local_registry("", None), "")

    @override_settings(CONTAINER_REGISTRY_URL="https://registry.example.com")
    def test_external_registry_url_stays_bare(self):
        self.assertEqual(_local_registry_host(), "")
        self.assertEqual(
            _with_local_registry("smsly/myapp:abc1234", None),
            "smsly/myapp:abc1234",
        )


class PushImageIdempotenceTests(TestCase):
    """push_image must not stack a second host prefix onto an already
    qualified name (would push a garbage repo and report success)."""

    def _push(self, image_name, registry_url="registry:5000"):
        from apps.cloud.services import builder as builder_mod

        client = MagicMock()
        client.images.get.return_value = MagicMock()
        client.images.push.return_value = iter([])
        # push_image() imports get_docker_client lazily — inject a fake
        # module so no daemon is touched.
        import sys
        fake_mod = SimpleNamespace(get_docker_client=lambda *a, **k: client)
        with patch.dict(sys.modules, {"apps.cloud.docker_client": fake_mod}):
            return builder_mod.NixpacksBuilder.push_image(
                image_name, registry_url, username="", password="")

    @override_settings(REGISTRY_USER="", REGISTRY_PASSWORD="")
    def test_qualified_name_pushes_as_is(self):
        remote_tag, err = self._push("registry:5000/smsly/myapp:abc1234")
        self.assertEqual(remote_tag, "registry:5000/smsly/myapp:abc1234")
        self.assertIsNone(err)

    @override_settings(REGISTRY_USER="", REGISTRY_PASSWORD="")
    def test_bare_name_gets_prefixed(self):
        remote_tag, err = self._push("smsly/myapp:abc1234")
        self.assertEqual(remote_tag, "registry:5000/smsly/myapp:abc1234")
        self.assertIsNone(err)


class PushPrefixStripTests(TestCase):
    """_push_image strips a local-registry prefix before pushing to a
    different host — otherwise the remote tag stacks hosts
    (`ext/registry:5000/ns/...`) while reporting success."""

    def setUp(self):
        from django.contrib.auth.models import User
        from apps.cloud.models import CloudProvider
        from apps.deployments.models import Deployment, Service
        self.user = User.objects.create_user(
            username="strip-user", password="password123")
        self.provider = CloudProvider.objects.create(
            name="strip-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="strip-svc", owner=self.user, provider=self.provider)
        self.deployment = Deployment.objects.create(
            service=self.service, commit_hash="abc1234")

    def test_qualified_local_name_pushes_bare_repo(self):
        from apps.deployments.services.pipeline.manager import (
            PipelineManager,
        )
        manager = PipelineManager(self.deployment)
        manager.image_name = "registry:5000/smsly/strip-svc:abc1234"
        tagged = {}

        fake_image = MagicMock()
        fake_image.tag.side_effect = lambda ref: tagged.setdefault("ref", ref)
        fake_client = MagicMock()
        fake_client.images.get.return_value = fake_image

        with patch.object(
            manager, "_resolve_push_credentials",
            return_value={"url": "registry.smsly.cloud",
                          "username": "u", "password": "p"},
        ), patch.object(
            manager, "_ensure_registry_login", return_value=None,
        ), patch(
            "apps.cloud.docker_client.get_docker_client",
            return_value=fake_client,
        ), patch(
            "apps.deployments.services.pipeline.registry."
            "NixpacksBuilder.push_image",
            return_value=("registry.smsly.cloud/smsly/strip-svc:abc1234",
                          None),
        ) as mock_push:
            manager._push_image()

        # Pushed as the bare repo — no stacked host.
        mock_push.assert_called_once()
        self.assertEqual(
            mock_push.call_args.args[0], "smsly/strip-svc:abc1234")
        self.assertEqual(tagged.get("ref"), "smsly/strip-svc:abc1234")
        self.assertEqual(
            manager.image_name,
            "registry.smsly.cloud/smsly/strip-svc:abc1234")
