# pylint: disable=invalid-name
"""Sidecar readiness gate: mesh failure must never fail a non-ecosystem deploy.

Regression for the prod incident where a broken Envoy sidecar (no SVID)
blocked healthy USER-service deployments until the operator disabled
mTLS by hand. Contract:
  * USER (non-ecosystem) service + sidecar not ready -> warn + continue.
  * ECOSYSTEM service + sidecar not ready -> hard fail (mesh mandatory).
  * Mesh disabled -> no inject; non-running sidecar corpses swept.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.deploy import deploy_container as dc
from apps.mtls.models import MtlsConfig


class SidecarDeployGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='gate-user', password='pwd')
        self.provider = CloudProvider.objects.create(
            name='gate-local',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def _service(self, name, managed_by="USER", mesh=True):
        svc = Service.objects.create(
            name=name, owner=self.user, provider=self.provider,
            managed_by=managed_by,
        )
        config = MtlsConfig.objects.get(service=svc)
        config.enabled = mesh
        config.sidecar_enabled = mesh
        config.save(update_fields=["enabled", "sidecar_enabled"])
        deployment = Deployment.objects.create(
            service=svc, status=Deployment.Status.BUILDING,
            commit_hash='gate1',
        )
        return svc, deployment

    def _deploy(self, deployment, wait_ready):
        compute = MagicMock()
        compute.deploy_container.return_value = MagicMock(resource_id='cid')
        with patch.object(dc, 'ComputeService', return_value=compute), \
                patch("apps.deployments.tasks.deploy.health._local_container_timeout_seconds",
                      return_value=1), \
                patch("apps.deployments.tasks.deploy.health._wait_for_local_container_healthy",
                      return_value=True), \
                patch.object(dc, '_regenerate_caddyfile', return_value=None), \
                patch("apps.deployments.tasks.deploy.health._wait_for_local_route_ready",
                      return_value=True), \
                patch.object(dc, '_run_managed_image_post_deploy_hooks', return_value=None), \
                patch.object(dc, '_probe_addon_connectivity', return_value=[]), \
                patch.object(dc, '_mark_deployment_active', return_value=None), \
                patch.object(dc, '_post_deploy_success', return_value=None), \
                patch.object(dc, 'broadcast_status', return_value=None), \
                patch("apps.deployments.tasks.deployment.tasks_deploy._post_deploy_monitor"), \
                patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.inject_sidecar") as mock_inject, \
                patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.wait_sidecar_ready",
                      return_value=wait_ready) as mock_wait, \
                patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_orphan_sidecar") as mock_sweep:
            dc._deploy_container(deployment, self.provider, 'img:1')
        return mock_inject, mock_wait, mock_sweep, compute

    def test_user_service_continues_when_sidecar_not_ready(self):
        _, deployment = self._service('gate-user-svc', mesh=True)
        mock_inject, mock_wait, mock_sweep, compute = self._deploy(
            deployment, wait_ready=False)
        mock_inject.assert_called_once()
        mock_wait.assert_called_once()
        compute.deploy_container.assert_called_once()
        deployment.refresh_from_db()
        self.assertEqual(deployment.status, Deployment.Status.ACTIVE)
        self.assertIn('MTLS-WARN', deployment.build_logs)

    def test_ecosystem_service_fails_when_sidecar_not_ready(self):
        _, deployment = self._service('gate-eco-svc', managed_by="ECOSYSTEM", mesh=True)
        with self.assertRaises(RuntimeError):
            self._deploy(deployment, wait_ready=False)

    def test_mesh_disabled_sweeps_orphans_without_inject(self):
        _, deployment = self._service('gate-off-svc', mesh=False)
        mock_inject, mock_wait, mock_sweep, compute = self._deploy(
            deployment, wait_ready=False)
        mock_inject.assert_not_called()
        mock_wait.assert_not_called()
        mock_sweep.assert_called_once()
        compute.deploy_container.assert_called_once()
