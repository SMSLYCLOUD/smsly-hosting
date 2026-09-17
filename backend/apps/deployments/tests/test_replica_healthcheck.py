"""Replica healthcheck parity: spawn must probe the real port/path.

Regression (2026-09-18): the autoscaler spawned
``smsly-frontend-pkorv-replica-01fed953`` with no Docker healthcheck, so
the daemon fell back to the image-baked probe (``:3000`` from the
Dockerfile) while the platform serves the app on ``:8000`` — the replica
reported unhealthy forever. ``spawn_local`` must inherit the live
reference's effective check (or build one from service config).
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.spawning_service import (
    SpawningService,
    _replica_healthcheck_spec,
)

REF_TEST = [
    "CMD-SHELL",
    'wget -qO- http://localhost:8000/health || exit 1',
]
REF_HC = {
    "Test": list(REF_TEST),
    "Interval": 30_000_000_000,
    "Timeout": 10_000_000_000,
    "Retries": 3,
    "StartPeriod": 120_000_000_000,
}


def _service(**overrides):
    svc = MagicMock()
    svc.name = "shop"
    svc.internal_port = 8000
    svc.health_check_path = "/health"
    svc.health_check_port = None
    svc.health_check_timeout = 10
    for key, value in overrides.items():
        setattr(svc, key, value)
    return svc


class ReplicaHealthcheckSpecTests(TestCase):
    def test_inherits_reference_check_verbatim(self):
        spec = _replica_healthcheck_spec(_service(), dict(REF_HC), {"PORT": "8000"})
        self.assertIsNotNone(spec)
        # Same port the primary probes...
        self.assertIn("8000", spec["test"][1])
        self.assertNotIn("3000", spec["test"][1])
        # ...timings preserved.
        self.assertEqual(spec["interval"], 30_000_000_000)
        self.assertEqual(spec["retries"], 3)

    def test_localhost_rewritten_to_loopback_ip(self):
        spec = _replica_healthcheck_spec(_service(), dict(REF_HC), {})
        self.assertNotIn("localhost", spec["test"][1])
        self.assertIn("127.0.0.1", spec["test"][1])

    def test_builds_from_service_config_without_reference(self):
        spec = _replica_healthcheck_spec(
            _service(), None, {"PORT": "8000"})
        self.assertIsNotNone(spec)
        self.assertEqual(spec["test"][0], "CMD-SHELL")
        self.assertIn("http://127.0.0.1:8000/health", spec["test"][1])

    def test_none_when_no_health_path_configured(self):
        spec = _replica_healthcheck_spec(
            _service(health_check_path=""), None, {"PORT": "8000"})
        self.assertIsNone(spec)

    def test_reference_without_test_falls_back_to_config(self):
        spec = _replica_healthcheck_spec(
            _service(), {"Interval": 1}, {"PORT": "8000"})
        self.assertIsNotNone(spec)
        self.assertIn("8000/health", spec["test"][1])


class SpawnLocalHealthcheckTests(TestCase):
    def _spawn(self, svc, client):
        with patch("docker.from_env", return_value=client), \
                patch("apps.deployments.models.PlatformConfig"), \
                patch("apps.deployments.services.spawning_service.ensure_scoped_network"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_runtime_for_container", return_value="runc"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_volumes", return_value={}):
            return SpawningService().spawn_local(svc, self._replica())

    @staticmethod
    def _replica():
        r = MagicMock()
        r.node = None
        r.id.hex = "abc12345"
        return r

    def _client(self, primary):
        client = MagicMock()
        client.containers.get.return_value = primary
        client.containers.list.return_value = []
        new_container = MagicMock()
        new_container.id = "new" * 21 + "x"
        client.containers.run.return_value = new_container
        return client

    def _primary(self, healthcheck):
        c = MagicMock()
        c.name = "shop"
        c.status = "running"
        c.image.tags = ["registry:5000/shop:latest"]
        c.attrs = {
            "Config": {
                "Env": ["PORT=8000"],
                "Labels": {
                    "traefik.http.services.shop.loadbalancer.server.port": "8000",
                },
                "Healthcheck": healthcheck,
            }
        }
        return c

    def _service(self):
        svc = MagicMock()
        svc.name = "shop"
        svc.id = "11111111-2222-3333-4444-555555555555"
        svc.project = None
        svc.docker_image = "registry:5000/shop:latest"
        svc.internal_port = 8000
        svc.public_domain = "shop.example.com"
        svc.memory_mb = 512
        svc.cpu_cores = 1.0
        svc.registry_credential_id = None
        svc.health_check_path = "/health"
        svc.health_check_port = None
        svc.health_check_timeout = 10
        svc.env_vars.all.return_value = []
        return svc

    def _test_blob(self, hc):
        # docker.types.Healthcheck capitalizes keys (Test/Interval/...);
        # plain spec dicts use lowercase.
        if isinstance(hc, dict):
            test = hc.get("test", hc.get("Test", []))
        else:
            test = hc.test
        return " ".join(str(part) for part in test)

    def test_spawn_passes_reference_healthcheck(self):
        client = self._client(self._primary(dict(REF_HC)))
        self._spawn(self._service(), client)

        kwargs = client.containers.run.call_args[1]
        hc = kwargs.get("healthcheck")
        self.assertIsNotNone(hc, "replica must carry a healthcheck")
        blob = self._test_blob(hc)
        self.assertIn("8000", blob)
        self.assertNotIn("3000", blob)

    def test_spawn_builds_healthcheck_without_reference(self):
        client = self._client(self._primary(None))
        self._spawn(self._service(), client)

        kwargs = client.containers.run.call_args[1]
        hc = kwargs.get("healthcheck")
        self.assertIsNotNone(hc, "replica must build a healthcheck from service config")
        blob = self._test_blob(hc)
        self.assertIn("8000/health", blob)
