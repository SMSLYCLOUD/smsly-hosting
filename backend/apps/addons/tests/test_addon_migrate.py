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
            'hostname': 'pg-old', 'username': 'u', 'database': 'db'}
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
