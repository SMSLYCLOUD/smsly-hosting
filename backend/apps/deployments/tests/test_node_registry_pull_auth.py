"""Tests for remote node registry credential syncing and pull resolution."""
import os
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.adapters.local import LocalAdapter
from apps.deployments.models import Project, RegistryCredential, Service
from apps.deployments.models.registry_scope import ScopedRegistry
from apps.deployments.services.provisioner.helpers import registry as reg_helper
from apps.deployments.services.registry_credentials import (
    resolve_pull_credentials,
    upsert_synced_pull_credentials,
)


class ScopedProjectTuplesTests(SimpleTestCase):
    def test_internal_scoped_registry_maps_only_to_node_url(self):
        project = mock.Mock()
        with mock.patch(
            "apps.deployments.models.registry_scope.ScopedRegistry.resolve_registry_credentials",
            return_value={"url": "registry:5000", "username": "u", "password": "p"},
        ), mock.patch(
            "apps.deployments.services.registry_routing.internal_registry_hosts",
            return_value=["registry:5000", "127.0.0.1:5000", "localhost:5000"],
        ):
            tuples = reg_helper._scoped_project_tuples(project, node_url="10.100.0.1:5000")
        self.assertEqual(tuples, [("10.100.0.1:5000", "u", "p")])

    def test_internal_scoped_registry_without_node_url_returns_empty(self):
        project = mock.Mock()
        with mock.patch(
            "apps.deployments.models.registry_scope.ScopedRegistry.resolve_registry_credentials",
            return_value={"url": "registry:5000", "username": "u", "password": "p"},
        ), mock.patch(
            "apps.deployments.services.registry_routing.internal_registry_hosts",
            return_value=["registry:5000", "127.0.0.1:5000", "localhost:5000"],
        ), mock.patch(
            "apps.deployments.services.registry_routing.master_registry_node_url",
            return_value="",
        ):
            tuples = reg_helper._scoped_project_tuples(project, node_url="")
        self.assertEqual(tuples, [])

    def test_external_scoped_registry_preserved(self):
        project = mock.Mock()
        with mock.patch(
            "apps.deployments.models.registry_scope.ScopedRegistry.resolve_registry_credentials",
            return_value={"url": "https://ghcr.io", "username": "u", "password": "p"},
        ), mock.patch(
            "apps.deployments.services.registry_routing.internal_registry_hosts",
            return_value=["registry:5000"],
        ):
            tuples = reg_helper._scoped_project_tuples(project, node_url="10.100.0.1:5000")
        self.assertEqual(tuples, [("ghcr.io", "u", "p")])


class SyncedPullCredentialsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="nodeadmin", email="nodeadmin@example.com", password="pass",
        )
        self.project = Project.objects.create(owner=self.admin, name="Default")
        self.service = Service.objects.create(
            owner=self.admin,
            project=self.project,
            name="demo-svc",
            deploy_type="DOCKER",
            docker_image="nginx:alpine",
        )

    @override_settings(REGISTRY_USER="smsly-registry", REGISTRY_PASSWORD="local-node-wrong-password")
    def test_node_mode_prefers_synced_master_creds_over_local_env(self):
        # Local auto-created internal ScopedRegistry on worker node has different creds.
        ct = ContentType.objects.get_for_model(Project)
        ScopedRegistry.objects.create(
            content_type=ct,
            object_id=self.project.id,
            registry_url="",
            username="proj-local",
            password="local-proj-password",
            is_internal=True,
            is_active=True,
        )
        stored = upsert_synced_pull_credentials(
            [
                {
                    "registry": "10.100.0.1:5000",
                    "namespace": "",
                    "username": "smsly-registry",
                    "password": "master-host-password",
                },
                {
                    "registry": "10.100.0.1:5000",
                    "namespace": "proj-9da4e64b",
                    "username": "proj-9da4e64b",
                    "password": "master-proj-password",
                },
            ],
            owner=self.admin,
        )
        self.assertEqual(stored, 2)

        with mock.patch.dict(os.environ, {"MODE": "node", "MASTER_MESH_IP": "10.100.0.1"}):
            # Namespace-specific credential wins over host default.
            host, user, pwd = resolve_pull_credentials(
                "10.100.0.1:5000/proj-9da4e64b/smsly-helper:abc",
                scope_obj=self.service,
            )
            self.assertEqual((host, user, pwd), ("10.100.0.1:5000", "proj-9da4e64b", "master-proj-password"))

            # Other namespaces on 10.100.0.1:5000 fall back to host-level synced cred.
            host2, user2, pwd2 = resolve_pull_credentials(
                "10.100.0.1:5000/proj-other/app:v1",
                scope_obj=self.service,
            )
            self.assertEqual((host2, user2, pwd2), ("10.100.0.1:5000", "smsly-registry", "master-host-password"))

            # Public Docker Hub refs return empty credentials.
            self.assertEqual(resolve_pull_credentials("nginx:alpine"), ("", "", ""))

    def test_synced_creds_excluded_from_user_registry_credential_api(self):
        upsert_synced_pull_credentials(
            [{"registry": "10.100.0.1:5000", "username": "smsly-registry", "password": "secret"}],
            owner=self.admin,
        )
        RegistryCredential.objects.create(
            owner=self.admin,
            name="My GHCR",
            provider="ghcr",
            registry_url="ghcr.io",
            username="me",
            password="pat",
        )
        client = APIClient()
        client.force_authenticate(user=self.admin)
        resp = client.get("/api/v1/registry-credentials/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get("results", data) if isinstance(data, dict) else data
        names = [item["name"] for item in items]
        self.assertEqual(names, ["My GHCR"])

    def test_node_exec_registry_sync_endpoint(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        resp = client.post(
            "/api/v1/node/registry/sync/",
            data={
                "credentials": [
                    {
                        "registry": "https://10.100.0.1:5000/",
                        "namespace": "",
                        "username": "smsly-registry",
                        "password": "synced-password-123",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["synced"], 1)
        self.assertEqual(
            resolve_pull_credentials("10.100.0.1:5000/smsly/app:latest"),
            ("10.100.0.1:5000", "smsly-registry", "synced-password-123"),
        )


class LocalAdapterPullAuthTests(SimpleTestCase):
    def test_pull_image_uses_resolved_credentials_and_auth_config(self):
        adapter = LocalAdapter.__new__(LocalAdapter)
        adapter.docker_client = mock.Mock()
        adapter._pull_scope_obj = None

        with mock.patch(
            "apps.deployments.services.registry_credentials.resolve_pull_credentials",
            return_value=("10.100.0.1:5000", "smsly-registry", "master-secret"),
        ):
            ok = adapter.pull_image("10.100.0.1:5000/proj-9da4e64b/smsly-helper:tag1")

        self.assertTrue(ok)
        adapter.docker_client.login.assert_called_once_with(
            username="smsly-registry",
            password="master-secret",
            registry="10.100.0.1:5000",
        )
        adapter.docker_client.images.pull.assert_called_once_with(
            "10.100.0.1:5000/proj-9da4e64b/smsly-helper:tag1",
            auth_config={"username": "smsly-registry", "password": "master-secret"},
        )


class SyncNodePullCredentialsHelperTests(SimpleTestCase):
    def test_sync_node_pull_credentials_tags_project_namespaces(self):
        server = mock.Mock()
        server.id = uuid.uuid4()
        server.name = "upcloud-full-1"
        orch = mock.Mock()
        orch.sync_registry_credentials.return_value = True

        with mock.patch.object(
            reg_helper,
            "_registry_credential_list",
            return_value=[
                ("10.100.0.1:5000", "smsly-registry", "host-pwd"),
                ("10.100.0.1:5000", "proj-9da4e64b", "proj-pwd"),
                ("ghcr.io", "octo", "pat"),
            ],
        ), mock.patch(
            "apps.deployments.services.registry_routing.master_registry_node_url",
            return_value="10.100.0.1:5000",
        ), mock.patch(
            "apps.deployments.services.registry_routing.internal_registry_hosts",
            return_value=["registry:5000"],
        ), mock.patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator",
            return_value=orch,
        ):
            ok = reg_helper.sync_node_pull_credentials(server)

        self.assertTrue(ok)
        orch.sync_registry_credentials.assert_called_once_with([
            {
                "registry": "10.100.0.1:5000",
                "namespace": "",
                "username": "smsly-registry",
                "password": "host-pwd",
            },
            {
                "registry": "10.100.0.1:5000",
                "namespace": "proj-9da4e64b",
                "username": "proj-9da4e64b",
                "password": "proj-pwd",
            },
            {
                "registry": "ghcr.io",
                "namespace": "",
                "username": "octo",
                "password": "pat",
            },
        ])
