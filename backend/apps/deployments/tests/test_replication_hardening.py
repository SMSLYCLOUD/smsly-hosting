from unittest import mock

import django.test

from apps.deployments.views.replication import ReplicationDeploySerializer


class ReplicationHardeningTests(django.test.TestCase):
    def test_replication_password_required_and_not_default(self):
        payload = {
            "mesh_id": "00000000-0000-0000-0000-000000000000",
            "db_password": "strong-db",
            "admin_password": "strong-admin",
        }
        ser = ReplicationDeploySerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn("replication_password", ser.errors)

        payload["replication_password"] = "repl_pass"
        ser = ReplicationDeploySerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn("replication_password", ser.errors)

        payload["replication_password"] = "correct-horse-battery-staple"
        ser = ReplicationDeploySerializer(data=payload)
        self.assertTrue(ser.is_valid(), ser.errors)


class ReplicationHealthTaskTests(django.test.TestCase):
    """check_replication_health_task must not spam or flap elections.

    Regression (2026-09-09): with Patroni never deployed, every 30s run
    logged 2x ERROR and forced an election re-check (terms racing past
    26600), because patroni_leader was never populated and the C3 bridge
    ran unconditionally.
    """

    def _run(self, health, cache=None):
        from django.core.cache.backends.locmem import LocMemCache

        from apps.deployments.tasks.data import tasks_replication as task_mod

        if cache is None:
            cache = LocMemCache("replication-test", {})
        mesh = mock.MagicMock()
        mesh.peers.filter.return_value.count.return_value = 2
        mesh.name = "default"
        with mock.patch(
            "apps.deployments.services.replication_service.ReplicationService"
        ) as mock_svc, mock.patch(
            "apps.deployments.models.mesh.MeshNetwork"
        ) as mock_mesh_model, mock.patch(
            "apps.deployments.services.election_service.ElectionService"
        ) as mock_election, mock.patch.object(
            task_mod, "_dispatch_replication_alert"
        ), mock.patch.object(
            task_mod, "cache", cache
        ):
            mock_svc.check_replication_health.return_value = health
            mock_mesh_model.objects.filter.return_value = [mesh]
            with self.assertLogs(
                "apps.deployments.tasks.data.tasks_replication", level="DEBUG"
            ) as logs:
                task_mod.check_replication_health_task()
        return logs.output, mock_election

    def _unreachable(self):
        return {
            "nodes": [
                {"name": "patroni1", "wg_address": "10.100.0.1",
                 "status": "UNREACHABLE: refused"},
                {"name": "patroni2", "wg_address": "10.100.0.2",
                 "status": "UNREACHABLE: refused"},
            ],
            "patroni_leader": None,
        }

    def test_absent_patroni_skips_election_bridge(self):
        logs, mock_election = self._run(self._unreachable())
        mock_election.get_or_create_cluster.assert_not_called()

    def test_absent_patroni_logs_error_once(self):
        from django.core.cache.backends.locmem import LocMemCache

        cache = LocMemCache("replication-test-shared", {})
        logs, _ = self._run(self._unreachable(), cache=cache)
        errors = [line for line in logs
                  if "ERROR" in line and "UNREACHABLE" in line]
        self.assertEqual(len(errors), 2)
        # Second consecutive run stays quiet (rate-limited), alerts aside.
        logs, _ = self._run(self._unreachable(), cache=cache)
        errors = [line for line in logs
                  if "ERROR" in line and "UNREACHABLE" in line]
        self.assertEqual(errors, [])

    def test_leaderless_patroni_triggers_bridge(self):
        health = {
            "nodes": [
                {"name": "patroni1", "wg_address": "10.100.0.1",
                 "status": "OK", "role": "replica"},
            ],
            "patroni_leader": None,
        }
        cluster = mock.MagicMock()
        cluster.leader = mock.MagicMock()
        with mock.patch(
            "apps.deployments.services.election_service.ElectionService"
        ) as mock_election:
            mock_election.get_or_create_cluster.return_value = cluster
            from apps.deployments.tasks.data import tasks_replication as task_mod

            with mock.patch(
                "apps.deployments.services.replication_service.ReplicationService"
            ) as mock_svc, mock.patch(
                "apps.deployments.models.mesh.MeshNetwork"
            ) as mock_mesh_model, mock.patch.object(
                task_mod, "_dispatch_replication_alert"
            ):
                mesh = mock.MagicMock()
                mesh.peers.filter.return_value.count.return_value = 2
                mock_svc.check_replication_health.return_value = health
                mock_mesh_model.objects.filter.return_value = [mesh]
                task_mod.check_replication_health_task()
        self.assertEqual(cluster.state, "ELECTION_NEEDED")
