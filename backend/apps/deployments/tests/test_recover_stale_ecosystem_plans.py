"""Regression tests for stale ecosystem-plan recovery freshness guard.

A plan whose own row was recently updated (e.g. the deploy task's
per-service preparation heartbeat) must never be treated as a ghost,
even when no deployment rows have been touched yet.
"""

from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.utils import timezone

from apps.deployments.tasks.recover_stale_ecosystem_plans import (
    recover_stale_ecosystem_plans,
)


def _plan(updated_minutes_ago: int, created_minutes_ago: int = 60) -> MagicMock:
    now = timezone.now()
    plan = MagicMock()
    plan.id = "plan-123"
    plan.status = "deploying"
    plan.project_id = "proj-1"
    plan.created_at = now - timedelta(minutes=created_minutes_ago)
    plan.updated_at = now - timedelta(minutes=updated_minutes_ago)
    plan.error_message = ""
    return plan


class TestRecoverStaleEcosystemPlansFreshness(TestCase):
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_fresh_plan_row_is_not_recovered(self, mock_plan_filter, mock_dep_filter):
        """A recently-updated plan is alive even with zero deployment activity."""
        plan = _plan(updated_minutes_ago=2)
        mock_plan_filter.return_value = [plan]
        mock_dep_filter.return_value.exists.return_value = False

        res = recover_stale_ecosystem_plans.run()

        self.assertEqual(res["recovered"], 0)
        self.assertEqual(res["kept_alive"], 1)
        plan.save.assert_not_called()

    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_truly_stale_plan_is_recovered(self, mock_plan_filter, mock_dep_filter):
        """An old, untouched plan with no activity is still failed."""
        plan = _plan(updated_minutes_ago=60)
        mock_plan_filter.return_value = [plan]
        mock_dep_filter.return_value.filter.return_value.exists.return_value = False

        res = recover_stale_ecosystem_plans.run()

        self.assertEqual(res["recovered"], 1)
        self.assertEqual(plan.status, "failed")
        plan.save.assert_called_once()

    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_slow_iteration_plan_is_not_recovered(
        self, mock_plan_filter, mock_dep_filter
    ):
        """A plan updated 20 min ago (slow single-service iteration, no row
        touches yet) is alive under the 30-min activity window."""
        plan = _plan(updated_minutes_ago=20)
        mock_plan_filter.return_value = [plan]
        mock_dep_filter.return_value.exists.return_value = False

        res = recover_stale_ecosystem_plans.run()

        self.assertEqual(res["recovered"], 0)
        self.assertEqual(res["kept_alive"], 1)
        plan.save.assert_not_called()

    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_stale_plan_with_recent_deployments_is_skipped(
        self, mock_plan_filter, mock_dep_filter
    ):
        """Recent ecosystem deployment activity still protects an old plan row."""
        plan = _plan(updated_minutes_ago=60)
        mock_plan_filter.return_value = [plan]
        mock_dep_filter.return_value.filter.return_value.exists.return_value = True

        res = recover_stale_ecosystem_plans.run()

        self.assertEqual(res["recovered"], 0)
        self.assertEqual(res["kept_alive"], 1)
        plan.save.assert_not_called()
        # The activity check must scope to ecosystem rows (hash OR
        # creation marker — the pipeline rewrites commit_hash to the
        # real SHA once cloning starts).
        mock_dep_filter.return_value.filter.assert_called_once()
