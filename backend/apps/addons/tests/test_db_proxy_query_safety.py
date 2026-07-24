# pylint: disable=invalid-name
"""Safety tests for ``DatabaseProxy.query``.

These tests exercise the SQL-injection-via-read-only-bypass defenses
without ever touching a real Postgres instance. The DB-bound work is
patched out so the validation/authorization logic can be exercised in
isolation.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.db_proxy import DatabaseProxy
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class DBProxyQuerySafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dptest",
            email="dptest@test.com",
            password="testpass123",
        )
        self.service = Service.objects.create(name="svc", owner=self.user)
        self.addon = Addon.objects.create(
            service=self.service,
            name="db",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgresql://test:test@db:5432/test",
        )
        self.proxy = DatabaseProxy(self.addon)

    def test_rejects_update(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "UPDATE foo SET bar=1",
                addon=self.addon, user=self.user,
            )

    def test_rejects_delete(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "DELETE FROM foo",
                addon=self.addon, user=self.user,
            )

    def test_rejects_drop(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "DROP TABLE foo",
                addon=self.addon, user=self.user,
            )

    def test_rejects_set_transaction(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "SET TRANSACTION READ WRITE; SELECT 1",
                addon=self.addon, user=self.user,
            )

    def test_rejects_multi_statement(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "SELECT 1; DROP TABLE foo",
                addon=self.addon, user=self.user,
            )

    def test_rejects_set_session(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "SET SESSION default_transaction_read_only = off; SELECT 1",
                addon=self.addon, user=self.user,
            )

    def test_rejects_insert(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "INSERT INTO foo (a) VALUES (1)",
                addon=self.addon, user=self.user,
            )

    def test_rejects_truncate(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "TRUNCATE foo",
                addon=self.addon, user=self.user,
            )

    def test_rejects_alter(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "ALTER TABLE foo ADD COLUMN x INT",
                addon=self.addon, user=self.user,
            )

    def test_rejects_create(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "CREATE TABLE foo (id INT)",
                addon=self.addon, user=self.user,
            )

    def test_rejects_grant(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "GRANT ALL ON foo TO PUBLIC",
                addon=self.addon, user=self.user,
            )

    def test_rejects_set_local(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "SET LOCAL statement_timeout = 0; SELECT 1",
                addon=self.addon, user=self.user,
            )

    def test_rejects_empty_sql(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "",
                addon=self.addon, user=self.user,
            )
        with self.assertRaises(ValueError):
            self.proxy.query(
                "   ",
                addon=self.addon, user=self.user,
            )

    def test_rejects_none_sql(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                None,
                addon=self.addon, user=self.user,
            )

    def test_rejects_multiple_semicolons(self):
        with self.assertRaises(ValueError):
            self.proxy.query(
                "SELECT 1;;",
                addon=self.addon, user=self.user,
            )

    def test_allows_select(self):
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['col'], 'rows': [['result']], 'count': 1}
            result = self.proxy.query(
                "SELECT 1",
                addon=self.addon, user=self.user,
            )
            self.assertEqual(result, {'columns': ['col'], 'rows': [['result']], 'count': 1})
            mock_exec.assert_called_once()

    def test_allows_select_with_cte(self):
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['col'], 'rows': [], 'count': 0}
            self.proxy.query(
                "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
                addon=self.addon, user=self.user,
            )
            mock_exec.assert_called_once()

    def test_allows_explain(self):
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['?column?'], 'rows': [[1]], 'count': 1}
            result = self.proxy.query(
                "EXPLAIN SELECT 1",
                addon=self.addon, user=self.user,
            )
            self.assertEqual(result['count'], 1)
            mock_exec.assert_called_once()

    def test_allows_select_for_update(self):
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['id'], 'rows': [], 'count': 0}
            self.proxy.query(
                "SELECT id FROM foo FOR UPDATE",
                addon=self.addon, user=self.user,
            )
            mock_exec.assert_called_once()

    def test_allows_trailing_semicolon(self):
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['?column?'], 'rows': [[1]], 'count': 1}
            result = self.proxy.query(
                "SELECT 1;",
                addon=self.addon, user=self.user,
            )
            self.assertEqual(result['count'], 1)
            mock_exec.assert_called_once()

    def test_owner_check(self):
        other = User.objects.create_user(
            username="other", email="other@test.com", password="x",
        )
        with self.assertRaises(PermissionError):
            self.proxy.query(
                "SELECT 1",
                addon=self.addon, user=other,
            )

    def test_superuser_bypasses_owner_check(self):
        admin = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="x",
        )
        with patch.object(self.proxy, '_execute_readonly') as mock_exec:
            mock_exec.return_value = {'columns': ['?column?'], 'rows': [[1]], 'count': 1}
            result = self.proxy.query(
                "SELECT 1",
                addon=self.addon, user=admin,
            )
            self.assertEqual(result['count'], 1)
            mock_exec.assert_called_once()

    def test_non_postgres_returns_empty(self):
        mongo_addon = Addon.objects.create(
            service=self.service,
            name="mongo",
            addon_type=Addon.Type.MONGODB,
            status=Addon.Status.ACTIVE,
            connection_url="mongodb://test:test@db:27017/test",
        )
        mongo_proxy = DatabaseProxy(mongo_addon)
        with patch.object(mongo_proxy, '_execute_readonly') as mock_exec:
            self.assertEqual(
                mongo_proxy.query("SELECT 1", addon=mongo_addon, user=self.user),
                {},
            )
            mock_exec.assert_not_called()
