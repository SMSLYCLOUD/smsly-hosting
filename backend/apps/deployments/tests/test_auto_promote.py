"""Unit tests for auto-promote/auto-review sweeps (ORM fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments


class TestAutoPromoteStagedDeployments(TestCase):
    def _run(self, rows):
        mgr = MagicMock()
        mgr.filter.return_value.select_related.return_value = rows
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12), \
             patch("apps.deployments.tasks.deploy.promote._do_promote") as mock_promote:
            return auto_promote_staged_deployments.run(), mock_promote

    def test_promotes_eligible_rows(self):
        row = MagicMock()
        res, mock_promote = self._run([row])
        self.assertEqual(res, {"promoted": 1})
        mock_promote.assert_called_once()

    def test_empty_sweep_returns_zero(self):
        res, mock_promote = self._run([])
        self.assertEqual(res, {"promoted": 0})
        mock_promote.assert_not_called()

    def test_provider_import_resolves(self):
        """Regression: the provider lookup must import (was .providers typo)."""
        import apps.deployments.tasks.deploy.provider as provider_module
        self.assertTrue(callable(provider_module._resolve_provider_for_service))

    @patch("apps.deployments.tasks.deploy.promote.append_log")
    @patch("apps.deployments.tasks.deploy.promote._do_promote")
    def test_missing_green_fails_row_fast(self, mock_promote, mock_log):
        """A green container that no longer exists can never promote:
        fail the row instead of error-spamming every 15 minutes."""
        from apps.deployments.models import Deployment as RealDeployment
        mgr = MagicMock()
        row = MagicMock()
        row.status = RealDeployment.Status.STAGED
        mgr.filter.return_value.select_related.return_value = [row]
        mock_promote.side_effect = RuntimeError("Green container abc not found - may have crashed")
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12):
            from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments as task
            res = task.run()
        self.assertEqual(res, {"promoted": 0})
        self.assertEqual(row.status, RealDeployment.Status.FAILED)
        self.assertIsNotNone(row.finished_at)
        row.save.assert_called_once()

    @patch("apps.deployments.tasks.deploy.promote.append_log")
    @patch("apps.deployments.tasks.deploy.promote._do_promote")
    def test_unhealthy_green_stays_staged(self, mock_promote, mock_log):
        """An unhealthy green may recover — leave the row STAGED."""
        from apps.deployments.models import Deployment as RealDeployment
        mgr = MagicMock()
        row = MagicMock()
        row.status = RealDeployment.Status.STAGED
        mgr.filter.return_value.select_related.return_value = [row]
        mock_promote.side_effect = RuntimeError("Green container is unhealthy - aborting promotion")
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12):
            from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments as task
            res = task.run()
        self.assertEqual(res, {"promoted": 0})
        self.assertEqual(row.status, RealDeployment.Status.STAGED)
        row.save.assert_not_called()
