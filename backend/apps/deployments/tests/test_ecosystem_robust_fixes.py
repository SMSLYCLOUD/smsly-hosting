"""Unit tests verifying robust ecosystem deployment wave monitoring and plan finalization."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.models import Deployment
from apps.deployments.models_ecosystem import EcosystemPlan
from apps.deployments.tasks_ecosystem import (
    _cancel_dependent_deployments,
    _cancel_unreleased_deployments,
    _finalize_ecosystem_plan,
    ecosystem_release_wave_task,
)


class TestEcosystemRobustFixes(TestCase):
    """Test suite for robust wave orchestration and EcosystemPlan finalization."""

    @patch("apps.deployments.models_ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_completed(self, mock_dep_filter, mock_plan_filter):
        """When all deployments in all waves succeed, plan status transitions to COMPLETED."""
        mock_plan = MagicMock()
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.ACTIVE},
            {"status": Deployment.Status.ACTIVE},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        self.assertEqual(mock_plan.status, EcosystemPlan.Status.COMPLETED)
        self.assertEqual(mock_plan.error_message, "")
        mock_plan.save.assert_called_once()

    @patch("apps.deployments.models_ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_failed(self, mock_dep_filter, mock_plan_filter):
        """When any deployment fails, plan status transitions to FAILED."""
        mock_plan = MagicMock()
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.ACTIVE},
            {"status": Deployment.Status.FAILED},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        self.assertEqual(mock_plan.status, EcosystemPlan.Status.FAILED)
        self.assertIn("1/2 service failures or cancellations", mock_plan.error_message)
        mock_plan.save.assert_called_once()

    @patch("apps.deployments.models_ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_in_progress(self, mock_dep_filter, mock_plan_filter):
        """If deployments are still in progress, finalization is deferred."""
        mock_plan = MagicMock()
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.BUILDING},
            {"status": Deployment.Status.ACTIVE},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        mock_plan.save.assert_not_called()

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_cancel_unreleased_deployments(self, mock_dep_filter):
        """Unreleased QUEUED deployments are marked CANCELLED when wave times out."""
        dep_1 = MagicMock(status=Deployment.Status.QUEUED, build_logs="")
        dep_2 = MagicMock(status=Deployment.Status.ACTIVE, build_logs="")
        mock_dep_filter.return_value = [dep_1, dep_2]

        waves = [["dep-0"], ["dep-1", "dep-2"]]
        cancelled_count = _cancel_unreleased_deployments(waves, from_wave_index=1, reason="timeout")

        self.assertEqual(cancelled_count, 1)
        self.assertEqual(dep_1.status, Deployment.Status.CANCELLED)
        self.assertIn("timeout", dep_1.build_logs)
        dep_1.save.assert_called_once()
        dep_2.save.assert_not_called()

    @patch("apps.deployments.tasks_ecosystem._finalize_ecosystem_plan")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks_ecosystem._rebuild_ecosystem_build_counter")
    def test_ecosystem_release_wave_task_final_wave_completion(
        self, mock_counter, mock_dep_filter, mock_finalize
    ):
        """When checking final wave index and all deployments completed, plan is finalized."""
        mock_dep_filter.return_value.values.return_value = [
            {"id": "dep-1", "status": Deployment.Status.ACTIVE}
        ]

        waves = [["dep-1"]]
        res = ecosystem_release_wave_task.run(
            provider_id="prov-1",
            waves=waves,
            wave_index=1,
            plan_id="plan-123",
        )

        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["waves"], 1)
        mock_finalize.assert_called_once_with("plan-123", waves)

    @patch("apps.deployments.tasks_ecosystem._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks_ecosystem._cancel_unreleased_deployments")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks_ecosystem._rebuild_ecosystem_build_counter")
    def test_ecosystem_release_wave_task_timeout(
        self, mock_counter, mock_dep_filter, mock_cancel_unreleased, mock_finalize
    ):
        """When wave recheck count exceeds max, remaining deployments are cancelled and plan is finalized."""
        mock_dep_filter.return_value.values.return_value = [
            {"id": "dep-1", "status": Deployment.Status.BUILDING}
        ]
        mock_cancel_unreleased.return_value = 2

        waves = [["dep-1"], ["dep-2", "dep-3"]]
        res = ecosystem_release_wave_task.run(
            provider_id="prov-1",
            waves=waves,
            wave_index=1,
            recheck_count=10,
            max_rechecks=10,
            plan_id="plan-123",
        )

        self.assertEqual(res["status"], "timed_out")
        mock_cancel_unreleased.assert_called_once_with(waves, 1, "ecosystem wave timed out")
        mock_finalize.assert_called_once_with("plan-123", waves)

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_cancel_dependent_deployments_only_cancels_downstream(self, mock_dep_filter):
        """Verify that when an upstream service fails, only its downstream dependents are cancelled."""
        dep_api = MagicMock(id="dep-api", status=Deployment.Status.QUEUED, build_logs="")
        dep_worker = MagicMock(id="dep-worker", status=Deployment.Status.QUEUED, build_logs="")

        def filter_side_effect(id__in):
            if "dep-api" in id__in:
                return [dep_api]
            return []

        mock_dep_filter.side_effect = filter_side_effect

        waves = [["dep-db"], ["dep-api", "dep-worker"]]
        dependencies = {
            "api": {"db"},
        }
        deployment_by_repo_key = {
            "db": "dep-db",
            "api": "dep-api",
            "worker": "dep-worker",
        }

        cancelled = _cancel_dependent_deployments(
            waves=waves,
            from_wave_index=1,
            failed_deployment_ids=["dep-db"],
            dependencies=dependencies,
            deployment_by_repo_key=deployment_by_repo_key,
            reason="upstream failed",
        )

        self.assertEqual(cancelled, 1)
        self.assertEqual(dep_api.status, Deployment.Status.CANCELLED)
        dep_api.save.assert_called_once()
        dep_worker.save.assert_not_called()

    @patch.object(ecosystem_release_wave_task.app, "send_task")
    @patch("apps.deployments.tasks_ecosystem._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks_ecosystem._cancel_dependent_deployments")
    @patch("apps.deployments.tasks_ecosystem._queue_wave")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks_ecosystem._rebuild_ecosystem_build_counter")
    def test_ecosystem_release_wave_cascade_failure_isolation(
        self, mock_counter, mock_dep_filter, mock_queue_wave, mock_cancel_dep, mock_finalize, mock_send_task
    ):
        """Verify wave release cancels only downstream dependents when cancel_others_on_failure=False."""
        mock_dep_filter.return_value.values.return_value = [
            {"id": "dep-db", "status": Deployment.Status.FAILED}
        ]
        mock_dep_filter.return_value.first.return_value = MagicMock(ecosystem_retry_count=1)
        mock_cancel_dep.return_value = 1
        mock_queue_wave.return_value = 1

        waves = [["dep-db"], ["dep-api", "dep-worker"]]
        res = ecosystem_release_wave_task.run(
            provider_id="prov-1",
            waves=waves,
            wave_index=1,
            dependencies={"api": {"db"}},
            deployment_by_repo_key={"db": "dep-db", "api": "dep-api", "worker": "dep-worker"},
            cancel_others_on_failure=False,
            plan_id="plan-123",
        )

        self.assertEqual(res["status"], "released")
        self.assertEqual(res["cancelled_dependents"], 1)
        mock_cancel_dep.assert_called_once()


