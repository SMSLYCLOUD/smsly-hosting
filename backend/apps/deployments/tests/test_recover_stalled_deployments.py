"""Unit tests for the stalled-deployment sweeper (ORM fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.models import Deployment
from apps.deployments.tasks.recover_stalled_deployments import (
    _project_has_live_plan,
    recover_stalled_deployments,
)

SWEEPABLE = "apps.deployments.tasks.recover_stalled_deployments"


def _row(status, project_id=None):
    service = MagicMock(project_id=project_id)
    return MagicMock(id="dep-1", status=status, build_logs="", service=service)


class TestRecoverStalledDeployments(TestCase):
    def _run_with_rows(self, rows, plan_exists=False):
        mgr = MagicMock()
        (mgr.select_related.return_value.filter.return_value
            .order_by.return_value.__getitem__.return_value) = rows
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter") as mock_plan_filter:
            mock_plan_filter.return_value.exists.return_value = plan_exists
            return recover_stalled_deployments.run(), mgr

    def test_stale_idle_row_cancelled(self):
        row = _row(Deployment.Status.BUILDING)
        res, _ = self._run_with_rows([row])
        self.assertEqual(res, {"cancelled": 1, "skipped_live_plan": 0})
        self.assertEqual(row.status, Deployment.Status.CANCELLED)
        self.assertIsNotNone(row.finished_at)
        self.assertIn("recover_stalled_deployments", row.build_logs)
        row.save.assert_called_once()

    def test_row_owned_by_live_plan_skipped(self):
        row = _row(Deployment.Status.QUEUED, project_id="proj-1")
        res, _ = self._run_with_rows([row], plan_exists=True)
        self.assertEqual(res, {"cancelled": 0, "skipped_live_plan": 1})
        self.assertEqual(row.status, Deployment.Status.QUEUED)
        row.save.assert_not_called()

    def test_empty_sweep_returns_zero(self):
        res, _ = self._run_with_rows([])
        self.assertEqual(res, {"cancelled": 0})

    def test_sweep_query_contract(self):
        mgr = MagicMock()
        (mgr.select_related.return_value.filter.return_value
            .order_by.return_value.__getitem__.return_value) = []
        with patch("apps.deployments.models.Deployment.objects", mgr):
            recover_stalled_deployments.run()
        kwargs = mgr.select_related.return_value.filter.call_args[1]
        swept = set(kwargs["status__in"])
        self.assertIn(Deployment.Status.QUEUED, swept)
        self.assertIn(Deployment.Status.BUILDING, swept)
        self.assertIn(Deployment.Status.DEPLOYING, swept)
        self.assertNotIn(Deployment.Status.AWAITING_APPROVAL, swept)
        self.assertNotIn(Deployment.Status.STAGED, swept)
        self.assertNotIn(Deployment.Status.FAILED, swept)
        self.assertNotIn(Deployment.Status.ACTIVE, swept)

    def test_plan_check_db_error_fails_open(self):
        row = _row(Deployment.Status.DEPLOYING, project_id="proj-1")
        mgr = MagicMock()
        (mgr.select_related.return_value.filter.return_value
            .order_by.return_value.__getitem__.return_value) = [row]
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter",
                   side_effect=Exception("db down")):
            res = recover_stalled_deployments.run()
        self.assertEqual(res, {"cancelled": 0, "skipped_live_plan": 1})
        self.assertEqual(row.status, Deployment.Status.DEPLOYING)
        row.save.assert_not_called()


class TestProjectHasLivePlan(TestCase):
    def test_none_project_id_is_not_live(self):
        self.assertFalse(_project_has_live_plan(None))

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_deploying_plan_is_live(self, mock_filter):
        mock_filter.return_value.exists.return_value = True
        self.assertTrue(_project_has_live_plan("proj-1"))
        mock_filter.assert_called_once_with(
            project_id="proj-1", status="deploying",
        )

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_finished_plan_is_not_live(self, mock_filter):
        mock_filter.return_value.exists.return_value = False
        self.assertFalse(_project_has_live_plan("proj-1"))
