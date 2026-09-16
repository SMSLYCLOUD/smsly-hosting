"""Tests for staging/live routing isolation.

Verifies that:
- Staged deployments get a staging-only Traefik router (not the live host_rule).
- Custom staging domains are excluded from the live CUSTOM_DOMAINS used in labels.
- Each service's staging domain generates an independent Caddy block.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docker
from django.test import SimpleTestCase

from apps.cloud.adapters.local import LocalAdapter


class StagingRoutingTests(SimpleTestCase):
    """Ensure staged containers are isolated from live routing."""

    def _build_adapter(self, has_live: bool = False):
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}

        live = None
        if has_live:
            live = MagicMock()
            docker_client.containers.get.return_value = live
        else:
            docker_client.containers.get.side_effect = docker.errors.NotFound("missing")

        created = MagicMock()
        created.id = "new-container-id"
        docker_client.containers.create.return_value = created

        adapter = object.__new__(LocalAdapter)
        adapter.mode = "AUTO"
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None
        return adapter, docker_client, live

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_staged_container_gets_staging_router_not_live_host(
        self, mock_filter, mock_load, _wait_mock,
    ):
        """When STAGING_DOMAIN is set and a live container exists, the new
        container should receive a staging-only Traefik router (named
        '{name}-staging') and NOT the live host_rule."""
        adapter, docker_client, _live = self._build_adapter(has_live=True)
        mock_load.return_value = SimpleNamespace(use_ssl=True)
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)

        adapter._deploy_docker(
            name="myapp",
            image="registry:5000/smsly/myapp:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "myapp.example.com",
                "STAGING_DOMAIN": "staging-myapp.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]

        # The staging router should exist with the staging domain
        self.assertEqual(
            labels.get("traefik.http.routers.myapp-staging.rule"),
            "Host(`staging-myapp.example.com`)",
        )
        self.assertEqual(labels.get("traefik.enable"), "true")

        # The LIVE host_rule must NOT appear on this container
        self.assertNotIn("traefik.http.routers.myapp.rule", labels)

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_staging_domain_excluded_from_custom_domains_host_rule(
        self, mock_filter, mock_load, _wait_mock,
    ):
        """When STAGING_DOMAIN is set, it should not appear in the
        live host_rule even if also listed in CUSTOM_DOMAINS."""
        adapter, docker_client, _live = self._build_adapter(has_live=False)
        mock_load.return_value = SimpleNamespace(use_ssl=True)
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)

        adapter._deploy_docker(
            name="myapp",
            image="registry:5000/smsly/myapp:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "myapp.example.com",
                "STAGING_DOMAIN": "staging-myapp.example.com",
                "CUSTOM_DOMAINS": "staging-myapp.example.com,custom.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]

        # First deploy: no live container, so no stage_before_cutover
        # But hold_for_staging=True, so it IS staged green
        # The staging router should use the staging domain
        self.assertEqual(
            labels.get("traefik.http.routers.myapp-staging.rule"),
            "Host(`staging-myapp.example.com`)",
        )

        # The staging domain should NOT leak into any live router
        self.assertNotIn("traefik.http.routers.myapp.rule", labels)

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_staged_container_metadata_labels(
        self, mock_filter, mock_load, _wait_mock,
    ):
        """Staged containers should carry the staging_domain metadata label
        for promote-time cleanup."""
        adapter, docker_client, _live = self._build_adapter(has_live=True)
        mock_load.return_value = SimpleNamespace(use_ssl=True)
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)

        adapter._deploy_docker(
            name="myapp",
            image="registry:5000/smsly/myapp:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "myapp.example.com",
                "STAGING_DOMAIN": "staging-myapp.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(
            labels.get("smsly.blue_green.staging_domain"),
            "staging-myapp.example.com",
        )

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_live_container_gets_live_router_not_staging(
        self, mock_filter, mock_load, _wait_mock,
    ):
        """When STAGING_DOMAIN is NOT set, a normal deploy gets the live
        host_rule — not a staging router."""
        adapter, docker_client, _live = self._build_adapter(has_live=False)
        mock_load.return_value = SimpleNamespace(use_ssl=True)
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)

        adapter._deploy_docker(
            name="myapp",
            image="registry:5000/smsly/myapp:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "myapp.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]

        # Should get the live router
        self.assertIn("traefik.http.routers.myapp.rule", labels)
        self.assertEqual(
            labels["traefik.http.routers.myapp.rule"],
            "Host(`myapp.example.com`)",
        )
        # Should NOT have a staging router
        self.assertNotIn("traefik.http.routers.myapp-staging.rule", labels)
        self.assertNotIn("smsly.blue_green.staging_domain", labels)


class HoldAutoPromoteGateTests(SimpleTestCase):
    """The adapter hold fast-path (blue_green_auto_promote) must honor the
    promotion readiness policy: ready → promote, blocked → hold green."""

    def _build_adapter(self, has_live: bool = True):
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}

        live = MagicMock()
        docker_client.containers.get.return_value = live

        created = MagicMock()
        created.id = "new-container-id"
        docker_client.containers.create.return_value = created

        adapter = object.__new__(LocalAdapter)
        adapter.mode = "AUTO"
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None
        adapter._service_id = "svc-1"
        return adapter, docker_client

    def _service_and_row(self):
        svc = SimpleNamespace(
            id="svc-1",
            is_public=True,
            deploy_strategy="ROLLING",
            canary_percentage=0,
            promotion_policy={},
        )
        row = SimpleNamespace(
            id="dep-1",
            status="BUILDING",
            green_container_id="",
            staged_at=None,
            commit_hash="abc123",
            service=svc,
        )
        return svc, row

    def _deploy_hold(self, adapter, mock_load):
        mock_load.return_value = SimpleNamespace(
            use_ssl=True,
            blue_green_staging_hold_seconds=30,
            blue_green_auto_promote=True,
        )
        return adapter._deploy_docker(
            name="myapp",
            image="registry:5000/smsly/myapp:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "myapp.example.com",
                "STAGING_DOMAIN": "staging-myapp.example.com",
            },
        )

    @patch("time.sleep")
    @patch.object(LocalAdapter, "promote_container", return_value="promoted-id")
    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.services.safedeploy.promotion_guard._green_container_state",
           return_value=("running", "healthy"))
    @patch("apps.deployments.models.deployment.Deployment.objects")
    @patch("apps.deployments.models.Service.objects.filter")
    @patch("apps.deployments.models.Service.objects.get")
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_hold_auto_promotes_when_ready(
        self, mock_load, mock_get, mock_filter, mock_dep_objects,
        _green_mock, _wait_mock, mock_promote, _sleep_mock,
    ):
        from apps.deployments.models import Service as _Svc

        adapter, _docker_client = self._build_adapter()
        svc, row = self._service_and_row()
        mock_get.return_value = svc
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)
        mock_dep_objects.filter.return_value.order_by.return_value.first.return_value = row
        self.assertIs(_Svc.objects.get, mock_get)

        result = self._deploy_hold(adapter, mock_load)

        mock_promote.assert_called_once_with("myapp", "new-container-id")
        self.assertEqual(result, "promoted-id")

    @patch("time.sleep")
    @patch.object(LocalAdapter, "promote_container", return_value="promoted-id")
    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.services.safedeploy.promotion_guard._green_container_state",
           return_value=("exited", ""))
    @patch("apps.deployments.models.deployment.Deployment.objects")
    @patch("apps.deployments.models.Service.objects.filter")
    @patch("apps.deployments.models.Service.objects.get")
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_hold_defers_to_review_when_blocked(
        self, mock_load, mock_get, mock_filter, mock_dep_objects,
        _green_mock, _wait_mock, mock_promote, _sleep_mock,
    ):
        """Unhealthy green (default require_green_healthy) holds the
        container for review instead of auto-promoting."""
        adapter, _docker_client = self._build_adapter()
        svc, row = self._service_and_row()
        mock_get.return_value = svc
        mock_filter.return_value.first.return_value = SimpleNamespace(is_public=True)
        mock_dep_objects.filter.return_value.order_by.return_value.first.return_value = row

        result = self._deploy_hold(adapter, mock_load)

        mock_promote.assert_not_called()
        _sleep_mock.assert_not_called()
        self.assertEqual(result, "new-container-id")
