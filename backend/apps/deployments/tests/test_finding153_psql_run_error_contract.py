# pylint: disable=invalid-name
"""Regression tests for Finding #153 (PostgresSnapshotManager error contract).

Before the fix, ``_run_psql`` and ``_run_psql_vars`` returned the raw
``subprocess.CompletedProcess`` and let ``CalledProcessError`` bubble
up. Each call site had to repeat the ``try/except`` boilerplate and
``subprocess.run`` errors leaked out as the wrong exception type in
some places. The fix wraps each ``subprocess.run`` in
``try/except CalledProcessError`` (and ``TimeoutExpired``) and returns
a ``SimpleNamespace`` carrying ``ok``, ``error``, ``stderr`` and
``stdout`` so callers can handle failures uniformly.

These tests verify:
  * On a clean run, the returned object has ``ok=True`` and exposes
    ``returncode``/``stdout``/``stderr``.
  * On ``CalledProcessError``, the returned object has ``ok=False``,
    ``error`` matching the exception message and ``stderr`` carrying
    the captured stderr.
  * On ``TimeoutExpired``, the returned object has ``ok=False`` and a
    non-empty ``error`` field.
"""

from types import SimpleNamespace
from unittest.mock import patch

import subprocess

from django.test import SimpleTestCase

from apps.deployments.services.safedeploy.postgres_snapshot_manager import (
    PostgresSnapshotManager,
)


class Finding153PsqlRunErrorContractTests(SimpleTestCase):
    def setUp(self):
        self.manager = PostgresSnapshotManager(
            admin_db_url="postgres://u:p@db:5432/postgres",
        )

    def test_success_returns_ok_true(self):
        """A clean subprocess.run yields ``ok=True`` with normal fields."""
        proc = SimpleNamespace(
            returncode=0, stdout="CREATE DATABASE", stderr="",
        )
        with patch("subprocess.run", return_value=proc) as mock_run:
            result = self.manager._run_psql(
                "postgres://u:p@db:5432/postgres",
                "SELECT 1",
            )

        mock_run.assert_called_once()
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "CREATE DATABASE")

    def test_called_process_error_returns_ok_false_with_stderr(self):
        """A non-zero exit surfaces as ``ok=False``/``error``/``stderr``."""
        err = subprocess.CalledProcessError(
            returncode=1,
            cmd=["psql", "-c", "SELECT bogus"],
            output="out",
            stderr="psql: error: relation \"bogus\" does not exist",
        )
        with patch("subprocess.run", side_effect=err):
            result = self.manager._run_psql_vars(
                "postgres://u:p@db:5432/postgres",
                "SELECT :" "bogus",
                {"bogus": "value"},
                check=True,
            )

        self.assertFalse(result.ok)
        self.assertIn("Command", result.error)
        self.assertEqual(
            result.stderr,
            'psql: error: relation "bogus" does not exist',
        )
        self.assertEqual(result.returncode, 1)

    def test_timeout_returns_ok_false(self):
        """A timeout surfaces as ``ok=False``/``error`` and no returncode."""
        err = subprocess.TimeoutExpired(
            cmd=["psql"], timeout=10,
        )
        with patch("subprocess.run", side_effect=err):
            result = self.manager._run_psql(
                "postgres://u:p@db:5432/postgres",
                "SELECT pg_sleep(60)",
                timeout=10,
            )

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error)
        self.assertIsNone(result.returncode)
