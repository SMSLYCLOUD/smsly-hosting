# pylint: disable=invalid-name
"""Tests for shared logical Postgres addons (one server, N databases)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.addons.services import shared_postgres
from apps.addons.services.shared_postgres import (
    _quote_ident,
    attach_alias,
    drop_logical_db,
    ensure_logical_db,
)
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


def _sqls(mock_psql):
    """SQL strings issued to shared_postgres._psql (arg 1; arg 0 is the db)."""
    return [c[0][1] for c in mock_psql.call_args_list]


class SqlQuotingTests(SimpleTestCase):
    def test_quote_ident_doubles_quotes(self):
        self.assertEqual(_quote_ident('a"b'), '"a""b"')
        self.assertEqual(_quote_ident('plain'), '"plain"')


class EnsureLogicalDbTests(SimpleTestCase):
    def _statements(self, mock_psql, role_exists=False, db_exists=False):
        def _fake(database, sql, timeout=60):
            if "FROM pg_roles" in sql:
                return "1" if role_exists else ""
            if "FROM pg_database" in sql:
                return "1" if db_exists else ""
            return ""

        mock_psql.side_effect = _fake
        return mock_psql

    @patch("apps.addons.services.shared_postgres._psql")
    def test_fresh_role_and_db(self, mock_psql):
        self._statements(mock_psql)
        ensure_logical_db("tenant_a", "tenant_a", "pw123")
        joined = "\n".join(_sqls(mock_psql))
        self.assertIn('CREATE ROLE "tenant_a"', joined)
        self.assertIn('CONNECTION LIMIT 10', joined)
        self.assertIn('statement_timeout', joined)
        self.assertIn('CREATE DATABASE "tenant_a" OWNER "tenant_a"', joined)
        # PUBLIC locked out, owner granted — the tenant-isolation core.
        self.assertIn('REVOKE CONNECT ON DATABASE "tenant_a" FROM PUBLIC', joined)
        self.assertIn('GRANT CONNECT ON DATABASE "tenant_a" TO "tenant_a"', joined)
        # System catalogs closed too (PUBLIC connects to them by default).
        self.assertIn('REVOKE CONNECT ON DATABASE "postgres" FROM "tenant_a"', joined)
        self.assertIn('CREATE EXTENSION IF NOT EXISTS vector', joined)
        self.assertIn('CREATE ROLE "tenant_a"', joined)

    @patch("apps.addons.services.shared_postgres._psql")
    def test_existing_role_and_db_skips_creates(self, mock_psql):
        self._statements(mock_psql, role_exists=True, db_exists=True)
        ensure_logical_db("tenant_a", "tenant_a", "newpw")
        joined = "\n".join(_sqls(mock_psql))
        self.assertNotIn('CREATE ROLE "tenant_a"', joined)
        self.assertNotIn('CREATE DATABASE "tenant_a"', joined)
        # Password always re-set so retries converge instead of drifting.
        self.assertIn("ALTER ROLE", joined)

    @patch("apps.addons.services.shared_postgres._psql")
    def test_drop_terminates_then_drops(self, mock_psql):
        drop_logical_db("tenant_a", "tenant_a")
        stmts = _sqls(mock_psql)
        self.assertTrue(any("pg_terminate_backend" in s for s in stmts))
        term_idx = next(i for i, s in enumerate(stmts) if "pg_terminate_backend" in s)
        drop_idx = next(i for i, s in enumerate(stmts) if "DROP DATABASE" in s)
        role_idx = next(i for i, s in enumerate(stmts) if "DROP ROLE" in s)
        self.assertLess(term_idx, drop_idx)
        self.assertLess(drop_idx, role_idx)


class HardenSystemCatalogsTests(SimpleTestCase):
    @patch("apps.addons.services.shared_postgres._psql")
    def test_public_loses_connect_on_system_dbs(self, mock_psql):
        from apps.addons.services.shared_postgres import _harden_system_catalogs
        _harden_system_catalogs()
        joined = "\n".join(_sqls(mock_psql))
        self.assertIn('REVOKE CONNECT ON DATABASE "postgres" FROM PUBLIC', joined)
        self.assertIn('REVOKE CONNECT ON DATABASE "template1" FROM PUBLIC', joined)


class AttachAliasTests(SimpleTestCase):
    @patch("apps.addons.services.shared_postgres.ensure_shared_server")
    @patch("apps.addons.services.shared_postgres._endpoint_aliases", return_value=["postgres-a"])
    @patch("apps.addons.services.shared_postgres._run")
    def test_already_attached_is_noop(self, mock_run, _aliases, _ensure):
        attach_alias("smsly-net-x", "postgres-a")
        mock_run.assert_not_called()

    @patch("apps.addons.services.shared_postgres.ensure_shared_server")
    @patch("apps.addons.services.shared_postgres._endpoint_aliases", return_value=[])
    @patch("apps.addons.services.shared_postgres._run")
    def test_missing_alias_connects(self, mock_run, _aliases, _ensure):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        attach_alias("smsly-net-x", "postgres-a")
        verbs = [c[0][0][2] for c in mock_run.call_args_list]
        self.assertIn("disconnect", verbs)
        self.assertIn("connect", verbs)
        connect_argv = mock_run.call_args[0][0]
        self.assertIn("postgres-a", connect_argv)

    @patch("apps.addons.services.shared_postgres.ensure_shared_server")
    @patch("apps.addons.services.shared_postgres._endpoint_aliases",
           return_value=["postgres-old"])
    @patch("apps.addons.services.shared_postgres._run")
    def test_reconnect_preserves_existing_aliases(self, mock_run, _aliases, _ensure):
        # `connect` on an attached endpoint silently drops the new alias,
        # so the code reconnects carrying the full set (2026-09-18: alias
        # never resolved until this was fixed).
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        attach_alias("smsly-net-x", "postgres-a")
        connect_argv = mock_run.call_args[0][0]
        self.assertIn("postgres-a", connect_argv)
        self.assertIn("postgres-old", connect_argv)


class ResolvePostgresModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sharedmode", password="x")
        self.service = Service.objects.create(name="modesvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service,
            name="postgres-modesvc",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
        )
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def _provisioner(self):
        from apps.addons.services.addon_provisioner import AddonProvisioner
        return AddonProvisioner()

    def _set_toggle(self, value: bool):
        from apps.deployments.models.platform import PlatformConfig
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={"postgres_shared_addons_default": value})
        PlatformConfig.clear_cache()

    def test_shared_sticks_even_when_toggle_off(self):
        self._set_toggle(False)
        addon = self._addon(provision_mode="shared")
        self.assertEqual(
            self._provisioner()._resolve_postgres_mode(addon, "whatever"), "shared")

    def test_existing_url_stays_container(self):
        addon = self._addon(
            connection_url="postgresql://u:p@smsly-addon-postgres-x:5432/db")
        self.assertEqual(
            self._provisioner()._resolve_postgres_mode(addon, "whatever"), "container")

    @patch("apps.addons.services.addon_provisioner.AddonProvisioner._container_status",
           return_value=(None, False))
    def test_fresh_addon_follows_toggle_on(self, _status):
        self._set_toggle(True)
        addon = self._addon()
        self.assertEqual(
            self._provisioner()._resolve_postgres_mode(addon, "whatever"), "shared")

    @patch("apps.addons.services.addon_provisioner.AddonProvisioner._container_status",
           return_value=(None, False))
    def test_fresh_addon_follows_toggle_off(self, _status):
        self._set_toggle(False)
        addon = self._addon()
        self.assertEqual(
            self._provisioner()._resolve_postgres_mode(addon, "whatever"), "container")

    def test_existing_container_stays_container(self):
        from apps.addons.services.addon_provisioner import AddonProvisioner
        with patch.object(AddonProvisioner, "_container_status",
                          return_value=("abc123", True)):
            addon = self._addon()
            self.assertEqual(
                self._provisioner()._resolve_postgres_mode(addon, "whatever"), "container")


class SharedDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="shareddel", password="x")
        self.service = Service.objects.create(name="delsvc", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="postgres-delsvc",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            provision_mode="shared",
            connection_url="postgresql://tenant_x:pw@postgres-delsvc:5432/tenant_x",
        )

    @patch("apps.addons.services.shared_postgres._psql", return_value="")
    def test_orchestrator_drops_logical_db(self, mock_psql):
        from apps.deployments.services.deletion_orchestrator import (
            DeletionOrchestrator,
        )
        orch = DeletionOrchestrator()
        orch.docker_client = MagicMock()
        self.assertTrue(orch.delete_addon_resources(self.addon))
        joined = "\n".join(_sqls(mock_psql))
        self.assertIn("DROP DATABASE", joined)
        self.assertIn("DROP ROLE", joined)


class SharedStandbyTests(SimpleTestCase):
    def setUp(self):
        patcher = patch(
            "apps.addons.services.shared_postgres._superuser_password",
            return_value="test-super-pw",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_ok(self, stdout=""):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = stdout
        proc.stderr = ""
        return proc

    @patch("apps.addons.services.shared_postgres._wait_container_ready")
    @patch("apps.addons.services.shared_postgres._primary_conninfo_ok", return_value=True)
    @patch("apps.addons.services.shared_postgres._ensure_replication_access")
    @patch("apps.addons.services.shared_postgres.ensure_shared_server")
    @patch("apps.addons.services.shared_postgres._run")
    def test_ensure_creates_and_seeds_standby(
            self, mock_run, _ensure, _repl, _streaming, _wait):
        from apps.addons.services import shared_postgres as sp

        # `docker ps` (list) vs `docker run` share argv[:2]; distinguish
        # by full command instead.
        def _route(cmd, timeout=60):
            if cmd[:3] == ["docker", "ps", "-a"]:
                return self._run_ok("")
            return self._run_ok("")
        mock_run.side_effect = _route

        self.assertEqual(sp.ensure_shared_standby(), sp.SHARED_STANDBY)
        run_cmds = [c[0][0] for c in mock_run.call_args_list
                    if c[0][0][:2] == ["docker", "run"]]
        self.assertEqual(len(run_cmds), 1)
        flat = " ".join(run_cmds[0])
        self.assertIn("pg_basebackup", flat)
        self.assertIn("-R", flat)
        self.assertIn(sp.SHARED_STANDBY, run_cmds[0])

    @patch("apps.addons.services.shared_postgres.ensure_shared_server")
    @patch("apps.addons.services.shared_postgres._run")
    def test_ensure_skips_create_when_present(self, mock_run, _ensure):
        from apps.addons.services import shared_postgres as sp

        def _route(cmd, timeout=60):
            if cmd[:3] == ["docker", "ps", "-a"]:
                return self._run_ok("abc123\n")
            return self._run_ok("")
        mock_run.side_effect = _route

        with patch.object(sp, "_wait_container_ready"), \
                patch.object(sp, "_primary_conninfo_ok", return_value=True), \
                patch.object(sp, "_ensure_replication_access"):
            self.assertEqual(sp.ensure_shared_standby(), sp.SHARED_STANDBY)
        run_cmds = [c[0][0] for c in mock_run.call_args_list
                    if c[0][0][:2] == ["docker", "run"]]
        self.assertEqual(run_cmds, [])

    def test_lag_none_when_not_streaming(self):
        from apps.addons.services import shared_postgres as sp

        with patch.object(sp, "_psql", return_value=""):
            self.assertIsNone(sp.shared_standby_lag_seconds())

    def test_lag_parses_seconds(self):
        from apps.addons.services import shared_postgres as sp

        with patch.object(sp, "_psql", return_value="0.42\n"):
            self.assertAlmostEqual(sp.shared_standby_lag_seconds(), 0.42)

    def test_promote_refuses_live_primary_without_force(self):
        from apps.addons.services import shared_postgres as sp

        with patch.object(sp, "_standby_running", return_value=True), \
                patch.object(sp, "_psql", return_value="f\n"):
            with self.assertRaises(RuntimeError) as ctx:
                sp.promote_shared_standby()
            self.assertIn("split-brain", str(ctx.exception))

    def test_promote_moves_aliases_and_renames(self):
        from apps.addons.services import shared_postgres as sp

        calls = []

        def _route(cmd, timeout=60):
            calls.append(cmd)
            return self._run_ok("")

        with patch.object(sp, "_standby_running", return_value=True), \
                patch.object(sp, "_psql", return_value="f\n"), \
                patch.object(sp, "_psql_on", return_value="f\n"), \
                patch.object(sp, "_container_networks",
                             return_value={"smsly-net": ["postgres-a"]}), \
                patch.object(sp, "_wait_container_ready"), \
                patch.object(sp, "_ensure_replication_access_on"), \
                patch("apps.addons.services.shared_postgres._run",
                      side_effect=_route):
            # force=True skips the liveness gate and goes straight to
            # fencing (the plain _psql mock above is then unused).
            self.assertEqual(sp.promote_shared_standby(force=True), sp.SHARED_CONTAINER)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("pg_ctl" in c and "promote" in c for c in flat))
        self.assertTrue(any("disconnect" in c for c in flat))
        connect = next(c for c in flat if "connect" in c.split())
        self.assertIn("postgres-a", connect)
        self.assertTrue(any("rename" in c for c in flat))

    def test_status_healthy_when_streaming(self):
        from apps.addons.services import shared_postgres as sp

        with patch.object(sp, "_container_running", return_value=True), \
                patch.object(sp, "_standby_running", return_value=True), \
                patch.object(sp, "shared_standby_lag_seconds", return_value=0.1):
            status = sp.shared_ha_status()
        self.assertEqual(status["state"], "HEALTHY")
        self.assertEqual(status["lag_seconds"], 0.1)

    def test_status_unknown_on_docker_failure(self):
        from apps.addons.services import shared_postgres as sp

        with patch.object(sp, "_container_running", side_effect=FileNotFoundError("x")):
            status = sp.shared_ha_status()
        self.assertEqual(status["state"], "UNKNOWN")
