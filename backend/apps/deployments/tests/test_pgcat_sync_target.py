# pylint: disable=invalid-name
"""Replica sync must never target the tenants pooler.

Regression: _find_pgcat_container's fallback matched any name containing
'pgcat' — while the platform pooler was restarting, a replica sync
rendered the platform config into pgcat-tenants' volume, crash-looping
it until manual repair.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class FindPgcatContainerTests(SimpleTestCase):
    def _client(self, names):
        client = MagicMock()
        existing = set(names)

        def _get(name):
            if name in existing:
                return MagicMock(name=name)
            import docker
            raise docker.errors.NotFound("No such container")

        listed = []
        for name in names:
            c = MagicMock()
            c.name = name
            listed.append(c)
        client.containers.get.side_effect = _get
        client.containers.list.return_value = listed
        return client

    def test_exact_platform_name_wins(self):
        from apps.deployments.services import database_replica_service as svc
        client = self._client(["smsly-hosting-pgcat-1", "smsly-hosting-pgcat-tenants-1"])
        with patch("docker.DockerClient", return_value=client):
            self.assertEqual(svc._find_pgcat_container(), "smsly-hosting-pgcat-1")

    def test_fallback_skips_tenants_pooler(self):
        from apps.deployments.services import database_replica_service as svc
        client = self._client(["smsly-hosting-pgcat-tenants-1"])
        with patch("docker.DockerClient", return_value=client):
            self.assertIsNone(svc._find_pgcat_container())

    def test_fallback_still_finds_unconventional_platform_name(self):
        from apps.deployments.services import database_replica_service as svc
        client = self._client(["custom-pgcat"])
        with patch("docker.DockerClient", return_value=client):
            self.assertEqual(svc._find_pgcat_container(), "custom-pgcat")


class SyncEnvTests(SimpleTestCase):
    """sync_pgcat_config must build a real env dict (list-unpack TypeError)."""

    def test_sync_builds_dict_env_and_writes(self):
        from apps.deployments.services import database_replica_service as svc
        client = MagicMock()
        container = MagicMock()
        container.attrs = {"Config": {"Env": ["PATH=/usr/bin", "DB_REPLICA_HOSTS=stale:5432"]}}
        container.exec_run.return_value = MagicMock(exit_code=0, output=b"")
        client.containers.get.return_value = container
        with patch("docker.DockerClient", return_value=client), \
             patch.object(svc, "_find_pgcat_container", return_value="smsly-hosting-pgcat-1"), \
             patch.object(svc, "replica_endpoints_for_pgcat", return_value="h1:5432"):
            result = svc.sync_pgcat_config(trigger_reload=False)
        self.assertTrue(result["config_written"])
        self.assertIsNone(result["error"])
        _, kwargs = container.exec_run.call_args
        env = kwargs.get("environment")
        self.assertIsInstance(env, dict)
        # Fresh value wins over the stale image env.
        self.assertEqual(env.get(svc.DB_REPLICA_HOSTS_ENV), "h1:5432")
        self.assertEqual(env.get("PATH"), "/usr/bin")
