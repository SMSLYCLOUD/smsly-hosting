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
        self.assertIn('CREATE EXTENSION IF NOT EXISTS vector', joined)

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
        argv = mock_run.call_args[0][0]
        self.assertIn("connect", argv)
        self.assertIn("postgres-a", argv)


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
