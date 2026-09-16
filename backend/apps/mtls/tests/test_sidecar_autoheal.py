"""Unit tests for EnvoySidecar.remount_if_stale + repair_stale_sidecars_task.

Covers the 2026-09-15 follow-up: the resolver fix healed NEW injections,
but nothing healed sidecars already running on the empty decoy (the
deploy path kept them via ``already_running``, the repair endpoint is
manual-only). remount_if_stale closes the deploy path; the beat task
closes the loop entirely (remount stale, remove orphans, skip
in-flight deploys).
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
import datetime

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.mtls.services.envoy_sidecar import EnvoySidecar

NAMESPACED = "smsly-spire_spire-ecosystem-agent-socket"
DECOY = "spire-ecosystem-agent-socket"


def _service(name="shop"):
    svc = MagicMock()
    svc.name = name
    return svc


class InjectRaceTests(TestCase):
    def test_create_conflict_degrades_to_already_running(self):
        """Beat + deploy racing the same create must not fail the deploy."""
        import docker

        svc = _service()
        svc.internal_port = 8000
        svc.mtls_config = MagicMock(trust_domain="ecosystem.local", spiffe_id="")
        client = MagicMock()
        client.containers.get.side_effect = Exception("not found")
        client.containers.run.side_effect = docker.errors.APIError(
            "Conflict. The container name is already in use", response=MagicMock(status_code=409), explanation="conflict",
        )
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client), \
            patch.object(EnvoySidecar, "_find_main_container", return_value=MagicMock(id="abc")), \
            patch.object(EnvoySidecar, "generate_config", return_value="cfg"), \
            patch.object(EnvoySidecar, "ensure_sidecar_image", return_value="img"):
            result = EnvoySidecar.inject_sidecar(svc)
        self.assertEqual(result["status"], "already_running")

    def test_compose_uses_resolved_volumes_not_decoy(self):
        """Compose injection must mount the namespaced socket, never the bare decoy."""
        svc = _service()
        svc.internal_port = 8000
        svc.mtls_config = MagicMock(enabled=True, trust_domain="ecosystem.local")
        with patch(
            "apps.deployments.services.mtls_integration.resolve_spire_volume_name",
            side_effect=lambda short: NAMESPACED if "socket" in short else "smsly-spire_spire-ecosystem-agent-svids",
        ):
            out = EnvoySidecar.inject_sidecar_compose(svc, {})
        svc_vols = out["services"][EnvoySidecar.get_sidecar_name(svc)]["volumes"]
        self.assertTrue(any(v.startswith(NAMESPACED + ":") for v in svc_vols))
        self.assertFalse(any(v.startswith(DECOY + ":") for v in svc_vols))
        self.assertIn(NAMESPACED, out["volumes"])


class RemountIfStaleTests(TestCase):
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.inject_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.check_socket_mount_healthy")
    def test_healthy_running_keeps_sidecar(self, mock_check, mock_inject):
        mock_check.return_value = {
            "healthy": True, "mounted": NAMESPACED,
            "expected": NAMESPACED, "reason": "socket mount current",
        }
        mock_inject.return_value = {"status": "already_running"}
        with patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar"
        ) as mock_remove:
            result = EnvoySidecar.remount_if_stale(_service())
        mock_remove.assert_not_called()
        mock_inject.assert_called_once()
        self.assertFalse(result["remounted"])

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.inject_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.check_socket_mount_healthy")
    def test_stale_running_recreates(self, mock_check, mock_status, mock_inject):
        mock_check.return_value = {
            "healthy": False, "mounted": DECOY,
            "expected": NAMESPACED, "reason": f"stale socket mount {DECOY}",
        }
        mock_status.return_value = {"status": "running"}
        mock_inject.return_value = {"status": "injected"}
        with patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar"
        ) as mock_remove:
            result = EnvoySidecar.remount_if_stale(_service())
        mock_remove.assert_called_once()
        mock_inject.assert_called_once()
        self.assertTrue(result["remounted"])

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.inject_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.check_socket_mount_healthy")
    def test_unavailable_falls_back_to_inject(self, mock_check, mock_inject):
        # Missing sidecar / dead daemon: nothing informed to recreate.
        mock_check.return_value = {
            "healthy": False, "mounted": None, "expected": "",
            "reason": "mount check unavailable: no docker",
        }
        mock_inject.return_value = {"status": "injected"}
        with patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar"
        ) as mock_remove:
            result = EnvoySidecar.remount_if_stale(_service())
        mock_remove.assert_not_called()
        mock_inject.assert_called_once()
        self.assertFalse(result["remounted"])

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.inject_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_orphan_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.check_socket_mount_healthy")
    def test_stale_corpse_swept_then_injected(
        self, mock_check, mock_status, mock_sweep, mock_inject
    ):
        mock_check.return_value = {
            "healthy": False, "mounted": None,
            "expected": NAMESPACED, "reason": "socket mount missing",
        }
        mock_status.return_value = {"status": "exited"}
        mock_inject.return_value = {"status": "injected"}
        with patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar"
        ) as mock_remove:
            result = EnvoySidecar.remount_if_stale(_service())
        mock_remove.assert_not_called()
        mock_sweep.assert_called_once()
        mock_inject.assert_called_once()
        self.assertTrue(result["remounted"])


class RepairStaleSidecarsTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="heal-user", password="password123")
        self.provider = CloudProvider.objects.create(
            name="heal-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def _svc(self, name, deploy_status=None):
        svc = Service.objects.create(
            name=name, owner=self.user, provider=self.provider)
        cfg = svc.mtls_config
        cfg.enabled = True
        cfg.sidecar_enabled = True
        cfg.trust_domain = "ecosystem.local"
        cfg.save()
        if deploy_status is not None:
            Deployment.objects.create(
                service=svc, status=deploy_status, commit_hash="abc1234")
        return svc

    @patch("apps.mtls.tasks.sync_svid_for_service")
    @patch("apps.mtls.tasks._ensure_spire_entry_best_effort")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remount_if_stale")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_stale_with_running_app_remounted(
        self, mock_get_client, mock_find, mock_state, mock_remount, mock_remove,
        mock_ensure, mock_sync
    ):
        from apps.mtls.tasks import repair_stale_sidecars_task

        svc = self._svc("heal-stale")
        mock_get_client.return_value = MagicMock()
        mock_get_client.return_value.containers.list.return_value = []
        mock_find.return_value = MagicMock(name="app")
        mock_state.return_value = {"status": "running"}
        mock_remount.return_value = {"status": "injected", "remounted": True}
        mock_sync.return_value = True
        result = repair_stale_sidecars_task.run()
        self.assertIn("heal-stale", result["remounted"])
        mock_remove.assert_not_called()

    def test_healed_sidecar_without_svid_reported(self):
        """Remounted but SVID-less stays visible in errors, not silent success."""
        from apps.mtls.tasks import repair_stale_sidecars_task

        svc = self._svc("heal-nosvid")
        with patch("apps.cloud.docker_client.get_docker_client") as mock_get_client, \
            patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container") as mock_find, \
            patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status") as mock_state, \
            patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remount_if_stale") as mock_remount, \
            patch("apps.mtls.tasks._ensure_spire_entry_best_effort"), \
            patch("apps.mtls.tasks.sync_svid_for_service", return_value=False):
            mock_get_client.return_value = MagicMock()
            mock_get_client.return_value.containers.list.return_value = []
            mock_find.return_value = MagicMock(name="app")
            mock_state.return_value = {"status": "running"}
            mock_remount.return_value = {"status": "injected", "remounted": True}
            result = repair_stale_sidecars_task.run()
        self.assertIn("heal-nosvid", result["remounted"])
        self.assertTrue(any("heal-nosvid" in e for e in result["errors"]))

    @patch("apps.mtls.tasks.sync_svid_for_service")
    @patch("apps.mtls.tasks._ensure_spire_entry_best_effort")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remove_sidecar")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_orphan_running_sidecar_removed(
        self, mock_get_client, mock_find, mock_state, mock_remove,
        mock_ensure, mock_sync
    ):
        from apps.mtls.tasks import repair_stale_sidecars_task

        svc = self._svc("heal-orphan")
        mock_get_client.return_value = MagicMock()
        mock_get_client.return_value.containers.list.return_value = []
        mock_find.return_value = None  # no app container
        mock_state.return_value = {"status": "running"}
        mock_remove.return_value = {"status": "removed"}
        result = repair_stale_sidecars_task.run()
        self.assertIn("heal-orphan", result["orphans_removed"])

    @patch("apps.mtls.tasks._ensure_spire_entry_best_effort")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remount_if_stale")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_in_flight_deploy_skipped(
        self, mock_get_client, mock_find, mock_remount, mock_ensure
    ):
        from apps.mtls.tasks import repair_stale_sidecars_task

        svc = self._svc("heal-flight", deploy_status=Deployment.Status.BUILDING)
        mock_get_client.return_value = MagicMock()
        mock_get_client.return_value.containers.list.return_value = []
        result = repair_stale_sidecars_task.run()
        self.assertIn("heal-flight", result["skipped_in_flight"])
        mock_find.assert_not_called()
        mock_remount.assert_not_called()

    @patch("apps.mtls.tasks.sync_svid_for_service")
    @patch("apps.mtls.tasks._ensure_spire_entry_best_effort")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.remount_if_stale")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_status")
    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_terminal_deploy_not_skipped(
        self, mock_get_client, mock_find, mock_state, mock_remount,
        mock_ensure, mock_sync
    ):
        from apps.mtls.tasks import repair_stale_sidecars_task

        svc = self._svc("heal-done", deploy_status=Deployment.Status.FAILED)
        # Age the deployment past the in-flight window.
        Deployment.objects.filter(service=svc).update(
            updated_at=timezone.now() - datetime.timedelta(hours=2))
        mock_get_client.return_value = MagicMock()
        mock_get_client.return_value.containers.list.return_value = []
        mock_find.return_value = MagicMock(name="app")
        mock_state.return_value = {"status": "running"}
        mock_remount.return_value = {
            "status": "already_running", "remounted": False}
        result = repair_stale_sidecars_task.run()
        self.assertNotIn("heal-done", result["skipped_in_flight"])
        mock_remount.assert_called_once()
