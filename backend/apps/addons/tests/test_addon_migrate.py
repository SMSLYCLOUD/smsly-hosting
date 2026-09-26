"""Addon migrate pre-flight checks (raise before touching docker)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.addon_migrate import migrate_addon_mode
from apps.deployments.models import Addon, Service

User = get_user_model()


class MigratePrecheckTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="migratepre", password="x")
        self.service = Service.objects.create(name="migsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u:pw@postgres-x:5432/db")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def test_bad_target_rejected(self):
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(self._addon().id), "whatever")

    def test_non_postgres_rejected(self):
        addon = self._addon(addon_type="REDIS")
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")

    def test_inactive_rejected(self):
        addon = self._addon(status=Addon.Status.FAILED)
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")

    def test_same_mode_rejected(self):
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(self._addon().id), "shared")

    def test_missing_url_rejected(self):
        addon = self._addon(connection_url="")
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")


class MigrateConcurrencyTests(TestCase):
    def test_concurrent_migration_rejected(self):
        user = User.objects.create_user(username="migconc", password="x")
        service = Service.objects.create(name="migconcsvc", owner=user)
        addon = Addon.objects.create(
            service=service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.MIGRATING, provision_mode="shared",
            connection_url="postgresql://u:pw@h:5432/db")
        with self.assertRaises(ValueError):
            migrate_addon_mode(str(addon.id), "container")


class MigrateQuiesceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="migq", password="x")
        self.service = Service.objects.create(name="migqsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u:pw@pg-old:5432/db")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def _provisioner(self, new_url="postgresql://nu:np@pg-new:5432/db"):
        prov = mock.MagicMock()
        prov.create_backup.return_value = "/tmp/dump.sql"
        prov.provision_dispatch.return_value = ("cid-new", new_url)
        prov.restore_backup.return_value = True
        prov._parse_connection_url.return_value = {
            'hostname': 'pg-old', 'username': 'u', 'password': 'pw',
            'port': 5432, 'database': 'db'}
        return prov

    def test_containers_stopped_before_dump(self):
        addon = self._addon()
        events = []
        prov = self._provisioner()
        prov.create_backup.side_effect = (
            lambda a: events.append(("dump", None)) or "/tmp/dump.sql")
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=["svc-c1"],
        ), mock.patch(
            "apps.addons.services.addon_migrate._stop_service_containers",
            side_effect=lambda names: events.append(("stop", list(names))),
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner", prov,
        ), mock.patch(
            "apps.addons.services.addon_migrate.verify_postgres_url",
            side_effect=lambda url: events.append(("verify", url)) or True,
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_logical_db",
        ), mock.patch(
            "apps.addons.services.addon_migrate._start_service_containers",
        ) as mock_start, mock.patch(
            "apps.addons.services.addon_migrate.container_network_aliases",
            return_value={},
        ):
            migrate_addon_mode(str(addon.id), "container")
        kinds = [k for k, _ in events]
        self.assertIn("stop", kinds)
        self.assertIn("dump", kinds)
        self.assertLess(kinds.index("stop"), kinds.index("dump"))
        mock_start.assert_not_called()

    def test_failed_migration_restarts_stopped_and_restores_row(self):
        addon = self._addon()
        prov = self._provisioner()
        prov.provision_dispatch.side_effect = RuntimeError("no target")
        started = []
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=["svc-c1"],
        ), mock.patch(
            "apps.addons.services.addon_migrate._stop_service_containers",
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner", prov,
        ), mock.patch(
            "apps.addons.services.addon_migrate._start_service_containers",
            side_effect=lambda names: started.append(list(names)),
        ):
            with self.assertRaises(RuntimeError):
                migrate_addon_mode(str(addon.id), "container")
        self.assertEqual(started, [["svc-c1"]])
        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.ACTIVE)
        self.assertEqual(addon.provision_mode, "shared")

    def test_drop_failure_is_warning_not_rollback(self):
        addon = self._addon()
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=[],
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner",
            self._provisioner(),
        ), mock.patch(
            "apps.addons.services.addon_migrate.verify_postgres_url",
            return_value=True,
        ), mock.patch(
            "apps.addons.services.addon_migrate.container_network_aliases",
            return_value={},
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_logical_db",
            side_effect=RuntimeError("drop boom"),
        ):
            result = migrate_addon_mode(str(addon.id), "container")
        self.assertEqual(result["status"], "ok")
        self.assertIn("source_cleanup_warning", result)
        addon.refresh_from_db()
        self.assertEqual(addon.provision_mode, "container")

    def test_shared_target_pushes_pooler_config_on_success(self):
        """A successful container->shared migration must push the tenant
        pooler config AFTER the row is ACTIVE.
        Regression for 2026-09-26: the nested provision's push ran while
        the row was MIGRATING (excluded from the render), so the pooler
        never learned the user and every connection failed with
        "no such user" / SASL authentication failed."""
        addon = self._addon(provision_mode="container")
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=[],
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner",
            self._provisioner(
                new_url="postgresql://u:pw@pg-shared:5432/db"),
        ), mock.patch(
            "apps.addons.services.addon_migrate.verify_postgres_url",
            return_value=True,
        ), mock.patch(
            "apps.addons.services.addon_migrate.container_network_aliases",
            return_value={},
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_database_only",
        ), mock.patch(
            "apps.addons.services.tenant_pooler.push_tenants_config",
            return_value={"ok": True, "pools": 1, "changed": True},
        ) as mock_push:
            result = migrate_addon_mode(str(addon.id), "shared")
        mock_push.assert_called_once_with()
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("pooler_push_warning", result)
        addon.refresh_from_db()
        self.assertEqual(addon.provision_mode, "shared")
        self.assertEqual(addon.status, Addon.Status.ACTIVE)

    def test_shared_target_push_failure_warns_not_rolls_back(self):
        """A failed post-migration pooler push must surface as a warning,
        not a rollback — the source backend is already gone."""
        addon = self._addon(provision_mode="container")
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=[],
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner",
            self._provisioner(
                new_url="postgresql://u:pw@pg-shared:5432/db"),
        ), mock.patch(
            "apps.addons.services.addon_migrate.verify_postgres_url",
            return_value=True,
        ), mock.patch(
            "apps.addons.services.addon_migrate.container_network_aliases",
            return_value={},
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_database_only",
        ), mock.patch(
            "apps.addons.services.tenant_pooler.push_tenants_config",
            return_value={"ok": False, "pools": 1, "error": "hopper down"},
        ) as mock_push:
            result = migrate_addon_mode(str(addon.id), "shared")
        mock_push.assert_called_once_with()
        self.assertEqual(result["status"], "ok")
        self.assertIn("pooler_push_warning", result)
        addon.refresh_from_db()
        self.assertEqual(addon.provision_mode, "shared")
        self.assertEqual(addon.status, Addon.Status.ACTIVE)

    def test_shared_target_orphan_dropped_on_failure(self):
        addon = self._addon(provision_mode="container")
        prov = self._provisioner(
            new_url="postgresql://newu:newp@pg-shared:5432/newdb")
        prov.restore_backup.return_value = False
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=[],
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner", prov,
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_logical_db",
        ) as mock_drop:
            with self.assertRaises(RuntimeError):
                migrate_addon_mode(str(addon.id), "shared")
        mock_drop.assert_called_once_with("newu", "newdb")
        addon.refresh_from_db()
        self.assertEqual(addon.provision_mode, "container")


class MigrateAuthPreservationTests(TestCase):
    """Migrating shared <-> container must preserve app auth details.

    Regression for 2026-09-22: after migration the service's
    ADDON-sourced DATABASE_URL still pointed at the dropped backend
    (sync only refreshed derived keys), and shared->container left
    pooler_routed=True stale on a dedicated container.
    """
    def setUp(self):
        self.user = User.objects.create_user(username="migcreds", password="x")
        self.service = Service.objects.create(name="migcredsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            pooler_routed=True,
            connection_url="postgresql://u:pw@pg-old:5432/db")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def _provisioner(self, new_url="postgresql://nu:np@pg-new:5432/db"):
        prov = mock.MagicMock()
        prov.create_backup.return_value = "/tmp/dump.sql"
        prov.provision_dispatch.return_value = ("cid-new", new_url)
        prov.restore_backup.return_value = True
        prov._parse_connection_url.return_value = {
            'hostname': 'pg-old', 'username': 'u', 'password': 'pw',
            'port': 5432, 'database': 'db'}
        return prov

    def _run_migrate(self, addon, prov):
        with mock.patch(
            "apps.addons.services.addon_migrate._service_container_names",
            return_value=[],
        ), mock.patch(
            "apps.addons.services.addon_provisioner.addon_provisioner", prov,
        ), mock.patch(
            "apps.addons.services.addon_migrate.verify_postgres_url",
            return_value=True,
        ), mock.patch(
            "apps.addons.services.addon_migrate.container_network_aliases",
            return_value={},
        ), mock.patch(
            "apps.addons.services.shared_postgres.drop_logical_db",
        ):
            return migrate_addon_mode(str(addon.id), "container")

    def test_addon_sourced_url_repointed_user_value_untouched(self):
        from apps.deployments.models import EnvironmentVariable
        addon = self._addon()
        EnvironmentVariable.objects.create(
            service=self.service, key="DATABASE_URL",
            value="postgresql://u:pw@pg-old:5432/db",
            is_secret=True, source="ADDON")
        EnvironmentVariable.objects.create(
            service=self.service, key="LEGACY_URL",
            value="postgresql://u:pw@pg-old:5432/db",
            is_secret=False, source="USER")
        self._run_migrate(addon, self._provisioner())
        db_url = EnvironmentVariable.objects.get(
            service=self.service, key="DATABASE_URL")
        self.assertEqual(db_url.value, "postgresql://nu:np@pg-new:5432/db")
        legacy = EnvironmentVariable.objects.get(
            service=self.service, key="LEGACY_URL")
        self.assertEqual(legacy.value, "postgresql://u:pw@pg-old:5432/db")

    def test_pooler_flag_cleared_on_container_target(self):
        addon = self._addon(pooler_routed=True)
        self._run_migrate(addon, self._provisioner())
        addon.refresh_from_db()
        self.assertEqual(addon.provision_mode, "container")
        self.assertFalse(addon.pooler_routed)


class MigrateCredentialPreservationTests(TestCase):
    """Migration must preserve auth details (user/password/host).

    The target backend is created with the SOURCE's credentials, so
    every existing copy keeps working and dump/restore role ownership
    resolves. Only the database name changes, and only on shared
    targets (temp staging name — the source DB is still live).
    """
    def setUp(self):
        self.user = User.objects.create_user(username="migpres", password="x")
        self.service = Service.objects.create(name="migpressvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            pooler_routed=False,
            connection_url="postgresql://alice:s3cret@pg-old:5432/appdb")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def _mocks(self, prov):
        return (
            mock.patch(
                "apps.addons.services.addon_migrate._service_container_names",
                return_value=[]),
            mock.patch(
                "apps.addons.services.addon_provisioner.addon_provisioner",
                prov),
            mock.patch(
                "apps.addons.services.addon_migrate.verify_postgres_url",
                return_value=True),
            mock.patch(
                "apps.addons.services.addon_migrate.container_network_aliases",
                return_value={}),
            mock.patch(
                "apps.addons.services.shared_postgres.drop_logical_db"),
            mock.patch(
                "apps.addons.services.shared_postgres.drop_database_only"),
        )

    def test_shared_to_container_keeps_full_url(self):
        addon = self._addon()
        seen = {}
        prov = mock.MagicMock()
        prov.create_backup.return_value = "/tmp/dump.sql"

        def _dispatch(a):
            seen["url"] = a.connection_url
            seen["mode"] = a.provision_mode
            return ("cid-new", a.connection_url)
        prov.provision_dispatch.side_effect = _dispatch
        prov.restore_backup.return_value = True
        prov._parse_connection_url.return_value = {
            'hostname': 'pg-old', 'username': 'alice', 'password': 's3cret',
            'port': 5432, 'database': 'appdb'}
        m1, m2, m3, m4, m5, m6 = self._mocks(prov)
        with m1, m2, m3, m4, m5, m6:
            result = migrate_addon_mode(str(addon.id), "container")
        self.assertEqual(result["status"], "ok")
        # Target provisioned with the ORIGINAL credentials.
        self.assertEqual(seen["mode"], "container")
        self.assertIn("alice:s3cret@pg-old", seen["url"])
        self.assertIn("/appdb", seen["url"])
        addon.refresh_from_db()
        self.assertEqual(
            addon.connection_url, "postgresql://alice:s3cret@pg-old:5432/appdb")

    def test_container_to_shared_preserves_user_pass_temp_db(self):
        addon = self._addon(provision_mode="container", pooler_routed=False)
        seen = {}
        prov = mock.MagicMock()
        prov.create_backup.return_value = "/tmp/dump.sql"

        def _dispatch(a):
            seen["url"] = a.connection_url
            return ("", a.connection_url)
        prov.provision_dispatch.side_effect = _dispatch
        prov.restore_backup.return_value = True
        prov._parse_connection_url.return_value = {
            'hostname': 'pg-old', 'username': 'alice', 'password': 's3cret',
            'port': 5432, 'database': 'appdb'}
        m1, m2, m3, m4, m5, m6 = self._mocks(prov)
        with m1, m2, m3, m4, m5, m6:
            result = migrate_addon_mode(str(addon.id), "shared")
        self.assertEqual(result["status"], "ok")
        from urllib.parse import urlparse as _up
        parts = _up(seen["url"])
        # Same user, password, host — only the db name is a temp staging name.
        self.assertEqual(parts.username, "alice")
        self.assertEqual(parts.password, "s3cret")
        self.assertEqual(parts.hostname, "pg-old")
        self.assertNotEqual(parts.path.lstrip("/"), "appdb")
        self.assertIn("__mig_", parts.path)
        addon.refresh_from_db()
        self.assertEqual(addon.connection_url, seen["url"])


class MigrateProvisionRoutingTests(TestCase):
    """Kept URLs must route into the credential-reusing provision paths
    (not fresh-random generation). Uses the real provision() with a
    missing container: shared->container recreates with the persisted
    user/password/db instead of minting new ones."""

    def test_kept_url_recreates_container_with_same_creds(self):
        from unittest import mock
        from django.contrib.auth import get_user_model
        from apps.deployments.models import Service
        from apps.deployments.models.addons import Addon
        from apps.addons.services.addon_provisioner import AddonProvisioner

        user = get_user_model().objects.create_user(
            username="migrate-route", password="x")
        service = Service.objects.create(name="migrroutesvc", owner=user)
        addon = Addon.objects.create(
            service=service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://alice:s3cret@pg-old:5432/appdb")
        provisioner = AddonProvisioner()
        captured = {}
        with mock.patch.object(
            AddonProvisioner, "_ensure_network", return_value=None,
        ), mock.patch.object(
            AddonProvisioner, "_container_status", return_value=(None, False),
        ), mock.patch.object(
            AddonProvisioner, "_connect_addon_networks", return_value=None,
        ), mock.patch.object(
            AddonProvisioner, "_provision_postgres",
            side_effect=lambda *a, **k: captured.update(
                call=(a, k)) or ("cid-1", "unused"),
        ):
            cid, url = provisioner.provision(addon)
        self.assertEqual(url, "postgresql://alice:s3cret@pg-old:5432/appdb")
        (_a, _kw) = captured["call"][0], captured["call"][1]
        self.assertEqual(_a[1], "s3cret")
        self.assertEqual(_kw.get("db_user"), "alice")
        self.assertEqual(_kw.get("db_name"), "appdb")
