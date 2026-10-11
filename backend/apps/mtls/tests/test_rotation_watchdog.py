"""Unit tests for the SPIRE rotation watchdog (2026-10-11 outage).

The outage: the ecosystem agent ran without --pid host/--cgroupns host
and without the docker socket, so every workload fetch failed and all
ecosystem SVIDs expired with zero alerts. These tests cover runtime-flag
detection, auto-heal + cooldown, coverage gaps, and freshness reporting.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.deployments.models import Service
from apps.mtls.models import MtlsConfig
from apps.mtls.tasks import (
    _recreate_spire_agent,
    _spire_agent_runtime,
    check_spire_rotation_task,
)

User = get_user_model()


def _container_mock(*, running=True, pid_host=True, cgroupns_host=True,
                    docker_sock=True):
    container = mock.Mock()
    container.status = "running" if running else "exited"
    container.attrs = {
        "HostConfig": {
            "PidMode": "host" if pid_host else "",
            "CgroupnsMode": "host" if cgroupns_host else "private",
        },
        "Mounts": ([{"Destination": "/var/run/docker.sock"}]
                   if docker_sock else []),
    }
    return container


def _client_mock(container):
    client = mock.Mock()
    client.containers.get.return_value = container
    return client


class AgentRuntimeTests(SimpleTestCase):
    def test_healthy_runtime(self):
        report = _spire_agent_runtime(
            "smsly-spire-agent-ecosystem",
            client=_client_mock(_container_mock()))
        self.assertTrue(all(report.values()))

    def test_stopped_agent(self):
        report = _spire_agent_runtime(
            "smsly-spire-agent-ecosystem",
            client=_client_mock(_container_mock(running=False)))
        self.assertFalse(any(report.values()))

    def test_outage_signature_detected(self):
        # Exact 2026-10-11 shape: running, but no pid/cgroupns/socket.
        report = _spire_agent_runtime(
            "smsly-spire-agent-ecosystem",
            client=_client_mock(_container_mock(
                pid_host=False, cgroupns_host=False, docker_sock=False)))
        self.assertTrue(report["running"])
        self.assertFalse(report["pid_host"])
        self.assertFalse(report["cgroupns_host"])
        self.assertFalse(report["docker_sock"])

    def test_never_raises(self):
        client = mock.Mock()
        client.containers.get.side_effect = Exception("docker down")
        report = _spire_agent_runtime("x", client=client)
        self.assertFalse(any(report.values()))


class RotationWatchdogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="spire-watch", password="x")
        self.service = Service.objects.create(
            name="watch-svc", owner=self.user, deploy_type="DOCKER")
        self.config = MtlsConfig.objects.get(service=self.service)
        self.config.enabled = True
        self.config.trust_domain = "ecosystem.local"
        self.config.svid_expiry = timezone.now() + datetime.timedelta(hours=6)
        self.config.last_rotation = timezone.now()
        self.config.save()

    def _healthy(self, mock_client, mock_list):
        mock_client.return_value = _client_mock(_container_mock())
        mock_list.return_value = [{"spiffe_id": {"path": "/service/watch-svc"},
                                   "selectors": []}]

    def test_healthy_domain_no_errors_no_heal(self):
        with mock.patch("apps.cloud.docker_client.get_docker_client") as mc, \
             mock.patch("apps.deployments.tasks_spiffe._list_spire_entries_from") as ml, \
             mock.patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container",
                        return_value=object()), \
             mock.patch("apps.mtls.tasks._recreate_spire_agent") as heal:
            self._healthy(mc, ml)
            report = check_spire_rotation_task.run()
        eco = report["domains"]["ecosystem.local"]
        self.assertTrue(eco["runtime_ok"])
        self.assertTrue(eco["server_ok"])
        self.assertEqual(eco["missing_entries"], [])
        self.assertEqual(eco["stale"], [])
        heal.assert_not_called()

    def test_broken_runtime_triggers_heal_then_cooldown(self):
        broken = _client_mock(_container_mock(
            pid_host=False, cgroupns_host=False, docker_sock=False))
        with mock.patch("apps.cloud.docker_client.get_docker_client",
                        return_value=broken), \
             mock.patch("apps.mtls.tasks._recreate_spire_agent",
                        return_value="started") as heal:
            first = check_spire_rotation_task.run()
            second = check_spire_rotation_task.run()
        # The shared mock breaks both domains — both heal, then cooldown.
        self.assertEqual(first["healed"], ["platform.local", "ecosystem.local"])
        self.assertEqual(heal.call_count, 2)
        self.assertEqual(second["healed"], [])
        self.assertTrue(any("cooldown" in e for e in second["errors"]))

    def test_missing_entry_reported(self):
        with mock.patch("apps.cloud.docker_client.get_docker_client") as mc, \
             mock.patch("apps.deployments.tasks_spiffe._list_spire_entries_from") as ml:
            mc.return_value = _client_mock(_container_mock())
            ml.return_value = []
            report = check_spire_rotation_task.run()
        eco = report["domains"]["ecosystem.local"]
        self.assertIn("watch-svc", eco["missing_entries"])

    def test_stale_local_service_reported_nonlocal_skipped(self):
        self.config.svid_expiry = timezone.now() - datetime.timedelta(hours=1)
        self.config.last_rotation = timezone.now() - datetime.timedelta(hours=5)
        self.config.save()
        with mock.patch("apps.cloud.docker_client.get_docker_client") as mc, \
             mock.patch("apps.deployments.tasks_spiffe._list_spire_entries_from") as ml, \
             mock.patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container",
                        return_value=object()):
            mc.return_value = _client_mock(_container_mock())
            ml.return_value = [{"spiffe_id": {"path": "/service/watch-svc"},
                                "selectors": []}]
            report = check_spire_rotation_task.run()
        self.assertIn("watch-svc", report["domains"]["ecosystem.local"]["stale"])
        with mock.patch("apps.cloud.docker_client.get_docker_client") as mc, \
             mock.patch("apps.deployments.tasks_spiffe._list_spire_entries_from") as ml, \
             mock.patch("apps.mtls.services.envoy_sidecar.EnvoySidecar._find_main_container",
                        return_value=None):
            mc.return_value = _client_mock(_container_mock())
            ml.return_value = [{"spiffe_id": {"path": "/service/watch-svc"},
                                "selectors": []}]
            report2 = check_spire_rotation_task.run()
        self.assertEqual(report2["domains"]["ecosystem.local"]["stale"], [])

    def test_recreate_never_raises(self):
        self.assertTrue(_recreate_spire_agent({}).startswith("error:"))
