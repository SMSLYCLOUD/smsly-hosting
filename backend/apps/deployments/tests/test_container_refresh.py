"""Unit tests for container refresh (Docker SDK fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.container_refresh import (
    ContainerRefreshError,
    _is_remote_service,
    _live_extra_hosts,
    _resolve_gvisor_extra_hosts,
    build_refresh_plan,
    recreate_with_fresh_env,
)


def _service():
    svc = MagicMock()
    svc.id = "svc-1"
    svc.name = "api"
    svc.cpu_cores = "2.0"
    svc.memory_mb = 2048
    svc.server_id = None
    svc.server = None
    svc.active_target_type = ""
    svc.active_host_ip = ""
    ev = MagicMock()
    ev.key = "FOO"
    ev.value = "bar"
    svc.env_vars.all.return_value = [ev]
    return svc


def _container(name="api"):
    c = MagicMock()
    c.id = "abc123def456"
    c.name = name
    c.status = "running"
    c.image.tags = ["registry:5000/api:latest"]
    c.attrs = {
        "Config": {"Image": "registry:5000/api:latest", "Labels": {"smsly.service_id": "svc-1"}},
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "Runtime": "runc",
            "Binds": [],
        },
        "NetworkSettings": {"Networks": {"proj-net": {"Aliases": ["api"]}}},
        "Mounts": [{"Type": "volume", "Source": "api-data", "Destination": "/data", "Mode": "rw"}],
    }
    return c


def _client(old):
    client = MagicMock()
    client.containers.get.return_value = old
    client.containers.list.return_value = [old]
    client.api.create_endpoint_config.side_effect = lambda aliases=None: {"aliases": aliases or []}
    new_container = MagicMock()
    new_container.id = "new999"
    new_container.status = "running"
    client.containers.create.return_value = new_container
    return client, new_container


class TestContainerRefresh(TestCase):
    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_dry_run_returns_plan_without_touching(self, mock_from_env, _mock_mtls):
        old = _container()
        client, _ = _client(old)
        mock_from_env.return_value = client
        plan = build_refresh_plan(_service(), old)
        self.assertEqual(plan["image"], "registry:5000/api:latest")
        self.assertEqual(plan["primary_network"], "proj-net")
        self.assertEqual(plan["networks"], ["proj-net"])
        old.stop.assert_not_called()
        client.containers.create.assert_not_called()

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_recreate_order_and_env(self, mock_from_env, _mock_mtls):
        old = _container()
        client, new_container = _client(old)
        mock_from_env.return_value = client
        res = recreate_with_fresh_env(_service())
        self.assertTrue(res["ok"])
        self.assertEqual(res["container"], "api")
        old.stop.assert_called_once()
        old.rename.assert_called_once_with("api-prev")
        create_kwargs = client.containers.create.call_args[1]
        self.assertEqual(create_kwargs["image"], "registry:5000/api:latest")
        self.assertEqual(create_kwargs["environment"]["FOO"], "bar")
        self.assertEqual(create_kwargs["network"], "proj-net")
        self.assertIn("proj-net", create_kwargs["networking_config"])
        self.assertEqual(create_kwargs["labels"], {"smsly.service_id": "svc-1"})
        new_container.start.assert_called_once()
        client.containers.get.return_value.remove.assert_called()

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_create_failure_rolls_back(self, mock_from_env, _mock_mtls):
        old = _container()
        client, _ = _client(old)
        client.containers.create.side_effect = Exception("boom")
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())
        # rollback renames back: api -> api-prev, then api-prev -> api
        self.assertEqual(old.rename.call_count, 2)
        old.rename.assert_any_call("api-prev")
        old.rename.assert_any_call("api")
        old.start.assert_called_once()

    @patch("docker.from_env")
    def test_no_container_raises(self, mock_from_env):
        client = MagicMock()
        client.containers.list.return_value = []
        client.containers.get.side_effect = Exception("nope")
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())

    def test_remote_service_refused(self):
        svc = _service()
        svc.server_id = "node-1"
        svc.server = MagicMock(is_primary=False, host="10.0.0.9")
        config = MagicMock(server_ip="10.0.0.1")
        with patch("apps.deployments.models.PlatformConfig.load", return_value=config):
            with self.assertRaises(ContainerRefreshError):
                recreate_with_fresh_env(svc)

    def test_local_service_with_server_assignment_allowed(self):
        """Regression: Service.server is "where hosted" — also set for
        LOCAL services (primary node record). apply-env must not refuse
        them as remote."""
        svc = _service()
        svc.server_id = "primary-1"
        svc.server = MagicMock(is_primary=True, host="10.0.0.1")
        config = MagicMock(server_ip="10.0.0.1")
        old = _container()
        client, _ = _client(old)
        with patch("apps.deployments.models.PlatformConfig.load", return_value=config), \
                patch("apps.deployments.services.mtls_integration.get_mtls_env_vars",
                      return_value={}), \
                patch("docker.from_env", return_value=client):
            res = recreate_with_fresh_env(svc)
        self.assertTrue(res["ok"])
        old.stop.assert_called_once()

    def test_fresh_env_merges_live_and_db(self):
        """Parity (2026-09-12 outage): live PORT/derived vars persist;
        DB rows win (apply-env purpose); HOSTNAME never copied."""
        from unittest.mock import MagicMock as _MM

        from apps.deployments.services.container_refresh import (
            _fresh_env,
            _live_container_env,
        )
        old = _container()
        old.attrs["Config"]["Env"] = [
            "PORT=80", "SECRET=live-secret", "HOSTNAME=old", "STALE=x",
        ]
        svc = _service()
        port_row = _MM()
        port_row.key = "PORT"
        port_row.value = "8000"
        svc.env_vars.all.return_value = [
            svc.env_vars.all.return_value[0], port_row,
        ]
        env = _fresh_env(svc, _live_container_env(old))
        self.assertEqual(env["PORT"], "8000")  # DB edit wins
        self.assertEqual(env["SECRET"], "live-secret")  # live persists
        self.assertEqual(env["STALE"], "x")
        self.assertEqual(env["FOO"], "bar")
        self.assertNotIn("HOSTNAME", env)

    def test_is_remote_service_locality(self):
        self.assertFalse(_is_remote_service(_service()))
        # primary server record -> local
        svc = _service()
        svc.server_id = "primary-1"
        svc.server = MagicMock(is_primary=True, host="10.0.0.1")
        with patch("apps.deployments.models.PlatformConfig.load",
                   return_value=MagicMock(server_ip="10.0.0.1")):
            self.assertFalse(_is_remote_service(svc))
        # foreign server record -> remote
        svc.server = MagicMock(is_primary=False, host="10.0.0.9")
        with patch("apps.deployments.models.PlatformConfig.load",
                   return_value=MagicMock(server_ip="10.0.0.1")):
            self.assertTrue(_is_remote_service(svc))
        # verified remote execution -> remote even without a server row
        svc = _service()
        svc.active_target_type = "remote"
        svc.active_host_ip = "10.0.0.9"
        self.assertTrue(_is_remote_service(svc))

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_exotic_mount_refuses_before_stop(self, mock_from_env, _mock_mtls):
        old = _container()
        old.attrs["Mounts"] = [{"Type": "tmpfs", "Source": "", "Destination": "/tmp"}]
        client, _ = _client(old)
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())
        old.stop.assert_not_called()


def _runsc_container():
    """platform-api shape (2026-09-14): runsc runtime, no hosts lifeline."""
    c = _container()
    c.attrs["HostConfig"]["Runtime"] = "runsc"
    c.attrs["HostConfig"].pop("ExtraHosts", None)
    c.attrs["NetworkSettings"] = {"Networks": {
        "smsly-net-96e85eee": {"Aliases": []},
        "smsly-platform-net": {"Aliases": ["api", "api.default.internal"]},
    }}
    return c


def _addon_row(addon_id="addon-1", url="redis://:pw@redis-shared:6379/0"):
    addon = MagicMock()
    addon.id = addon_id
    addon.name = "redis-shared"
    addon.addon_type = "REDIS"
    addon.connection_url = url
    return addon


def _client_with_addon(old, addon_ip="172.30.224.2"):
    client, new_container = _client(old)
    addon_container = MagicMock()
    addon_container.attrs = {"NetworkSettings": {"Networks": {
        "smsly-net-96e85eee": {"IPAddress": addon_ip},
    }}}
    orig_return = client.containers.get.return_value

    def _get(name):
        if str(name).startswith("smsly-addon-"):
            return addon_container
        return orig_return

    client.containers.get.side_effect = _get
    return client, new_container, addon_container


class TestGvisorExtraHosts(TestCase):
    def test_live_entries_parsed(self):
        old = _container()
        old.attrs["HostConfig"]["ExtraHosts"] = ["redis-shared:172.30.224.2"]
        self.assertEqual(
            _live_extra_hosts(old), ["redis-shared:172.30.224.2"]
        )
        self.assertEqual(_live_extra_hosts(_container()), [])

    def test_fresh_resolution_wins_over_stale_live(self):
        svc = _service()
        svc.project_id = "proj-1"
        with patch("apps.deployments.models.addons.Addon") as mock_addon_cls:
            mock_addon_cls.objects.filter.return_value.filter.return_value = [
                _addon_row()
            ]
            old = _runsc_container()
            client, _, _ = _client_with_addon(old, addon_ip="172.30.224.9")
            hosts = _resolve_gvisor_extra_hosts(
                svc, {"smsly-net-96e85eee", "smsly-platform-net"},
                client, live=["redis-shared:172.30.224.2"],
            )
        self.assertEqual(hosts, ["redis-shared:172.30.224.9"])

    def test_db_failure_falls_back_to_live(self):
        svc = _service()
        with patch(
            "apps.deployments.models.addons.Addon",
            side_effect=Exception("no db"),
        ):
            hosts = _resolve_gvisor_extra_hosts(
                svc, {"smsly-net-96e85eee"}, MagicMock(),
                live=["redis-shared:172.30.224.2"],
            )
        self.assertEqual(hosts, ["redis-shared:172.30.224.2"])

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_runsc_recreate_injects_extra_hosts(self, mock_from_env, _mock_mtls):
        svc = _service()
        svc.project_id = "proj-1"
        old = _runsc_container()
        client, _, _ = _client_with_addon(old)
        mock_from_env.return_value = client
        with patch("apps.deployments.models.addons.Addon") as mock_addon_cls:
            mock_addon_cls.objects.filter.return_value.filter.return_value = [
                _addon_row()
            ]
            res = recreate_with_fresh_env(svc)
        self.assertTrue(res["ok"])
        create_kwargs = client.containers.create.call_args[1]
        self.assertEqual(create_kwargs["runtime"], "runsc")
        self.assertEqual(
            create_kwargs["extra_hosts"], ["redis-shared:172.30.224.2"]
        )

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_runc_recreate_carries_no_extra_hosts(self, mock_from_env, _mock_mtls):
        old = _container()  # runc fixture
        old.attrs["HostConfig"]["ExtraHosts"] = ["stale:10.0.0.9"]
        client, _ = _client(old)
        mock_from_env.return_value = client
        res = recreate_with_fresh_env(_service())
        self.assertTrue(res["ok"])
        create_kwargs = client.containers.create.call_args[1]
        self.assertEqual(create_kwargs["runtime"], "runc")
        self.assertNotIn("extra_hosts", create_kwargs)

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_force_default_runtime_drops_runsc_and_hosts(self, mock_from_env, _mock_mtls):
        old = _runsc_container()
        old.attrs["HostConfig"]["ExtraHosts"] = ["redis-shared:172.30.224.2"]
        client, _ = _client(old)
        mock_from_env.return_value = client
        res = recreate_with_fresh_env(_service(), force_default_runtime=True)
        self.assertTrue(res["ok"])
        create_kwargs = client.containers.create.call_args[1]
        self.assertNotIn("runtime", create_kwargs)
        self.assertNotIn("extra_hosts", create_kwargs)
