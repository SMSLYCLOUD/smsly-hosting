"""spawn_local parity tests: replica must mirror the live primary.

Regression for the 2026-09-12 outage where a partial Traefik service
block + missing PORT env took the whole service dark in Traefik.
Docker SDK and DB-backed helpers are mocked; no DB, no daemon.
"""
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.replica_parity import ReplicaParityError
from apps.deployments.services.spawning_service import SpawningService

PRIMARY_LABELS = {
    "traefik.enable": "true",
    "traefik.docker.network": "smsly-net",
    "traefik.http.services.shop.loadbalancer.server.port": "80",
    "traefik.http.services.shop.loadbalancer.healthcheck.path": "/health",
    "traefik.http.services.shop.loadbalancer.healthcheck.interval": "30s",
    "traefik.http.services.shop.loadbalancer.healthcheck.timeout": "300s",
    "traefik.http.services.shop.loadbalancer.healthcheck.hostname": "shop.example.com",
    "traefik.http.routers.shop.rule": "Host(`shop.example.com`)",
}
PRIMARY_ENV = ["PORT=80", "DATABASE_URL=postgres://live/db", "HOSTNAME=shop"]


def _primary_container():
    c = MagicMock()
    c.name = "shop"
    c.status = "running"
    c.image.tags = ["registry:5000/shop:latest"]
    c.attrs = {"Config": {"Env": list(PRIMARY_ENV), "Labels": dict(PRIMARY_LABELS)}}
    return c


def _service(db_rows=None):
    svc = MagicMock()
    svc.name = "shop"
    svc.id = "11111111-2222-3333-4444-555555555555"
    svc.project = None
    svc.docker_image = "registry:5000/shop:latest"
    svc.internal_port = 80
    svc.public_domain = "shop.example.com"
    svc.memory_mb = 512
    svc.cpu_cores = 1.0
    svc.registry_credential_id = None
    rows = []
    for key, value in (db_rows or {}).items():
        ev = MagicMock()
        ev.key = key
        ev.value = value
        rows.append(ev)
    svc.env_vars.all.return_value = rows
    return svc


def _replica():
    r = MagicMock()
    r.node = None
    r.id.hex = "abc12345"
    return r


class SpawnLocalParityTests(TestCase):
    def _spawn(self, svc, client):
        with patch("docker.from_env", return_value=client), \
                patch("apps.deployments.models.PlatformConfig"), \
                patch("apps.deployments.services.spawning_service.ensure_scoped_network"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_runtime_for_container", return_value="runc"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_volumes", return_value={}):
            return SpawningService().spawn_local(svc, _replica())

    def _client(self, primary, siblings=()):
        client = MagicMock()
        client.containers.get.return_value = primary
        client.containers.list.return_value = list(siblings)
        new_container = MagicMock()
        new_container.id = "new" * 21 + "x"
        client.containers.run.return_value = new_container
        return client, new_container

    def test_copies_env_and_identical_service_block(self):
        primary = _primary_container()
        client, _ = self._client(primary)
        self._spawn(_service(), client)

        kwargs = client.containers.run.call_args[1]
        env = kwargs["environment"]
        # Live PORT + secrets persist even though DB rows are empty.
        self.assertEqual(env["PORT"], "80")
        self.assertEqual(env["DATABASE_URL"], "postgres://live/db")
        self.assertNotIn("HOSTNAME", env)
        labels = kwargs["labels"]
        for key, value in PRIMARY_LABELS.items():
            if key.startswith("traefik.http.services.shop."):
                self.assertEqual(labels.get(key), value)
        # ...except the network, which is the replica's own.
        self.assertEqual(labels["traefik.docker.network"],
                         kwargs["network"])
        self.assertNotIn("traefik.http.routers.shop.rule", labels)

    def test_db_rows_override_live_env(self):
        primary = _primary_container()
        client, _ = self._client(primary)
        self._spawn(_service({"PORT": "8000", "EXTRA": "1"}), client)
        env = client.containers.run.call_args[1]["environment"]
        self.assertEqual(env["PORT"], "8000")
        self.assertEqual(env["EXTRA"], "1")
        self.assertEqual(env["DATABASE_URL"], "postgres://live/db")

    def test_conflicting_sibling_fails_closed(self):
        primary = _primary_container()
        stale = MagicMock()
        stale.name = "shop-replica-old"
        stale.status = "running"
        stale.attrs = {"Config": {"Env": [], "Labels": {
            "traefik.http.services.shop.loadbalancer.server.port": "80",
        }}}
        client, _ = self._client(primary, siblings=[stale])
        # call for real and expect the raise (no container created)
        with patch("docker.from_env", return_value=client), \
                patch("apps.deployments.models.PlatformConfig"), \
                patch("apps.deployments.services.spawning_service.ensure_scoped_network"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_runtime_for_container", return_value="runc"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_volumes", return_value={}):
            with self.assertRaises(ReplicaParityError):
                SpawningService().spawn_local(_service(), _replica())
        client.containers.run.assert_not_called()


class RemoteSpawnParityTests(TestCase):
    def test_copies_follower_service_block_and_env(self):
        svc = _service()
        svc.env_vars.all.return_value = []
        node = MagicMock()
        node.name = "worker-1"
        follower_labels = {
            "traefik.enable": "true",
            "traefik.http.services.shop.loadbalancer.server.port": "80",
            "traefik.http.services.shop.loadbalancer.healthcheck.path": "/health",
        }
        follower_config = {
            "Env": ["PORT=80", "DATABASE_URL=postgres://follower/db",
                    "HOSTNAME=shop"],
            "Labels": follower_labels,
        }
        ssh = MagicMock()

        def _exec(cmd, raise_on_error=False, timeout=300):
            if "docker inspect" in cmd and "shop" in cmd and "replica" not in cmd:
                return json.dumps(follower_config), "", 0
            if "docker inspect" in cmd:
                return "deadbeef" * 8, "", 0
            return "", "", 0

        ssh.exec_command.side_effect = _exec
        with patch("apps.deployments.models.PlatformConfig"), \
                patch.object(SpawningService, "_get_ssh", return_value=ssh), \
                patch.object(SpawningService, "_check_node_capacity", return_value=True), \
                patch("apps.deployments.services.spawning_service._detect_remote_runtime", return_value=""), \
                patch("apps.deployments.services.registry_routing.image_ref_for_node", side_effect=lambda x: x), \
                patch("apps.deployments.services.spawning_service._scoped_network_for", return_value="net1"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_args", return_value=""):
            replica = MagicMock()
            replica.node = node
            replica.id.hex = "abc12345"
            SpawningService().spawn(svc, node, replica)

        run_cmds = [c.args[0] for c in ssh.exec_command.call_args_list
                    if "docker run" in c.args[0]]
        self.assertEqual(len(run_cmds), 1)
        run_cmd = run_cmds[0]
        # Copied service block (not just server.port).
        self.assertIn(
            "traefik.http.services.shop.loadbalancer.healthcheck.path=/health",
            run_cmd)
        # Live PORT + secrets carried over; HOSTNAME never copied.
        self.assertIn("PORT=80", run_cmd)
        self.assertIn("DATABASE_URL=postgres://follower/db", run_cmd)
        self.assertNotIn("HOSTNAME", run_cmd)
        self.assertEqual(replica.status, "RUNNING")

    def test_missing_follower_falls_back_with_minimal_labels(self):
        svc = _service()
        node = MagicMock()
        node.name = "worker-1"
        ssh = MagicMock()

        def _exec(cmd, raise_on_error=False, timeout=300):
            if "docker inspect" in cmd:
                return "", "no such container", 1
            return "", "", 0

        ssh.exec_command.side_effect = _exec
        with patch("apps.deployments.models.PlatformConfig"), \
                patch.object(SpawningService, "_get_ssh", return_value=ssh), \
                patch.object(SpawningService, "_check_node_capacity", return_value=True), \
                patch("apps.deployments.services.spawning_service._detect_remote_runtime", return_value=""), \
                patch("apps.deployments.services.registry_routing.image_ref_for_node", side_effect=lambda x: x), \
                patch("apps.deployments.services.spawning_service._scoped_network_for", return_value="net1"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_args", return_value=""):
            replica = MagicMock()
            replica.node = node
            replica.id.hex = "abc12345"
            SpawningService().spawn(svc, node, replica)
        run_cmds = [c.args[0] for c in ssh.exec_command.call_args_list
                    if "docker run" in c.args[0]]
        self.assertEqual(len(run_cmds), 1)
        self.assertIn(
            "traefik.http.services.shop.loadbalancer.server.port=80",
            run_cmds[0])
