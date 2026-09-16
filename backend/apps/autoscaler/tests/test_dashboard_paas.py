"""Tests for dashboard PaaS wiring (2026-09-16 autoscaler page outage).

* ``collect_container_stats(include=...)`` restricts SDK stats calls to
  relevant containers (daemon hosts ~100 addon/sidecar containers;
  statting all of them over a loaded socket-proxy trips the 20s cap
  with mostly timeouts — 18s, zero results, dashboard 503).
* ``_overlay_paas_state`` replaces hardcoded workers=1 and legacy
  1/4 ceilings with 1 + RUNNING ServiceReplica rows and the
  service's own min/max on exact name match. Never raises.
* ``_run_autoscaler_check`` writes the STATUS cache (page 503s
  without it) with real worker counts.
* ``autoscaler_trigger`` dispatches the real PaaS engine sweep —
  the dashboard check alone only records advice.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.autoscaler.engine import container_metrics as cm
from apps.autoscaler.models.replica import ServiceReplica
from apps.autoscaler.views import dashboard as dash
from apps.deployments.models import Service

User = get_user_model()


def _stats_payload():
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": 200},
                      "system_cpu_usage": 1000, "online_cpus": 2},
        "precpu_stats": {"cpu_usage": {"total_usage": 100},
                         "system_cpu_usage": 500},
        "memory_stats": {"usage": 100 * 1024 * 1024,
                         "limit": 400 * 1024 * 1024},
        "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
        "pids_stats": {"current": 3},
    }


def _container(name):
    container = MagicMock()
    container.name = name
    container.stats.return_value = _stats_payload()
    return container


class CollectPredicateTests(TestCase):
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_include_restricts_stats_calls(self, mock_get_client):
        client = MagicMock()
        client.containers.list.return_value = [
            _container("keep-me"), _container("skip-me")]
        mock_get_client.return_value = client

        out = cm.collect_container_stats(include=lambda n: n == "keep-me")

        self.assertIn("keep-me", out)
        self.assertNotIn("skip-me", out)

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_none_collects_everything(self, mock_get_client):
        client = MagicMock()
        client.containers.list.return_value = [
            _container("aaa"), _container("bbb")]
        mock_get_client.return_value = client
        # Force the SDK path: no docker CLI / k8s in this process.
        with patch.object(cm, "_docker_stats_cli", return_value=None), \
                patch.object(cm, "k8s_available", return_value=False):
            out = cm.collect_container_stats()
        self.assertIn("aaa", out)
        self.assertIn("bbb", out)


class PaasOverlayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="paas", password="p")
        self.service = Service.objects.create(
            name="paas-svc", owner=self.user, deploy_type="DOCKER",
            docker_image="registry:5000/paas-svc:latest",
            min_replicas=2, max_replicas=8,
        )

    def _entry(self):
        return {"current_workers": 1, "min_workers": 1, "max_workers": 4}

    def test_overlay_reports_real_workers_and_ceilings(self):
        ServiceReplica.objects.create(
            service=self.service, node=None,
            container_name="paas-svc-replica-1", status="RUNNING")
        ServiceReplica.objects.create(
            service=self.service, node=None,
            container_name="paas-svc-replica-2", status="RUNNING")
        ServiceReplica.objects.create(
            service=self.service, node=None,
            container_name="paas-svc-replica-old", status="DESTROYED")
        services = {"paas-svc": self._entry()}
        dash._overlay_paas_state(services)
        self.assertEqual(services["paas-svc"]["current_workers"], 3)
        self.assertEqual(services["paas-svc"]["min_workers"], 2)
        self.assertEqual(services["paas-svc"]["max_workers"], 8)

    def test_overlay_zero_replicas_still_fixes_ceilings(self):
        services = {"paas-svc": self._entry()}
        dash._overlay_paas_state(services)
        self.assertEqual(services["paas-svc"]["current_workers"], 1)
        self.assertEqual(services["paas-svc"]["max_workers"], 8)

    def test_overlay_leaves_unknown_containers_alone(self):
        services = {"mystery-1": self._entry()}
        dash._overlay_paas_state(services)
        self.assertEqual(services["mystery-1"]["current_workers"], 1)
        self.assertEqual(services["mystery-1"]["max_workers"], 4)


class RunCheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="runcheck", password="p")
        self.service = Service.objects.create(
            name="dash-svc", owner=self.user, deploy_type="DOCKER",
            docker_image="registry:5000/dash-svc:latest",
            min_replicas=1, max_replicas=6,
        )
        ServiceReplica.objects.create(
            service=self.service, node=None,
            container_name="dash-svc-replica-1", status="RUNNING")

    def test_check_writes_status_cache_with_real_workers(self):
        from django.core.cache import cache
        canned = {
            "dash-svc": {
                "cpu_percent": 10.0, "memory_mb": 100.0,
                "memory_limit_mb": 500.0, "memory_percent": 20.0,
                "net_rx_mb": 1.0, "net_tx_mb": 2.0, "pids": 3,
            }
        }
        with patch.object(
            dash, "collect_container_stats", return_value=dict(canned)
        ), patch.object(
            dash, "_classify_container", return_value=("gunicorn", "dash")
        ):
            out = dash._run_autoscaler_check()
        self.assertEqual(out["services"]["dash-svc"]["current_workers"], 2)
        self.assertEqual(out["services"]["dash-svc"]["max_workers"], 6)
        cached = cache.get(dash.CACHE_KEY_STATUS)
        self.assertIsNotNone(cached)
        self.assertEqual(
            cached["services"]["dash-svc"]["current_workers"], 2)


class TriggerDispatchTests(TestCase):
    def test_trigger_dispatches_paas_engine(self):
        admin = User.objects.create_superuser(
            username="trigger-admin", email="t@test.com", password="p")
        from rest_framework.test import APIRequestFactory, force_authenticate
        req = APIRequestFactory().post("/api/v1/autoscaler/trigger/")
        force_authenticate(req, user=admin)
        with patch.object(
            dash, "_run_autoscaler_check",
            return_value={"status": "active", "services": {},
                          "recent_decisions": [], "budget": {}}), \
            patch("apps.autoscaler.services.tasks_autoscale"
                  ".analyze_all_services_task") as mock_task:
            resp = dash.autoscaler_trigger(req)
        mock_task.delay.assert_called_once_with()
        self.assertEqual(resp.status_code, 200)
