"""Unit tests verifying robust ecosystem deployment wave monitoring and plan finalization."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.models import Deployment
from apps.deployments.models.ecosystem import EcosystemPlan
from apps.deployments.tasks.ecosystem.tasks import (
    _cancel_dependent_deployments,
    _cancel_unreleased_deployments,
    _capture_pre_ecosystem_snapshot,
    _ecosystem_plan_still_deploying,
    _finalize_ecosystem_plan,
    ecosystem_deferred_build_task,
    ecosystem_release_wave_task,
)


class TestEcosystemRobustFixes(TestCase):
    """Test suite for robust wave orchestration and EcosystemPlan finalization."""

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
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

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
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

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
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
    @patch("apps.deployments.models.Deployment.objects.bulk_update")
    def test_cancel_unreleased_deployments(self, mock_bulk_update, mock_dep_filter):
        """Unreleased QUEUED deployments are marked CANCELLED when wave times out."""
        dep_1 = MagicMock(status=Deployment.Status.QUEUED, build_logs="")
        dep_2 = MagicMock(status=Deployment.Status.ACTIVE, build_logs="")
        mock_dep_filter.return_value = [dep_1, dep_2]

        waves = [["dep-0"], ["dep-1", "dep-2"]]
        cancelled_count = _cancel_unreleased_deployments(waves, from_wave_index=1, reason="timeout")

        self.assertEqual(cancelled_count, 1)
        self.assertEqual(dep_1.status, Deployment.Status.CANCELLED)
        self.assertIn("timeout", dep_1.build_logs)
        mock_bulk_update.assert_called_once()
        dep_2.save.assert_not_called()

    @patch("apps.deployments.tasks.ecosystem.tasks._finalize_ecosystem_plan")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.tasks._rebuild_ecosystem_build_counter")
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

    @patch("apps.deployments.tasks.ecosystem.tasks._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks.ecosystem.tasks._cancel_unreleased_deployments")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.tasks._rebuild_ecosystem_build_counter")
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
    @patch("apps.deployments.models.Deployment.objects.bulk_update")
    def test_cancel_dependent_deployments_only_cancels_downstream(self, mock_bulk_update, mock_dep_filter):
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
        mock_bulk_update.assert_called_once()
        dep_worker.save.assert_not_called()

    @patch.object(ecosystem_release_wave_task.app, "send_task")
    @patch("apps.deployments.tasks.ecosystem.tasks._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks.ecosystem.tasks._cancel_dependent_deployments")
    @patch("apps.deployments.tasks.ecosystem.tasks._queue_wave")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.tasks._rebuild_ecosystem_build_counter")
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

        # Since the fail-fast cascade removal (fb249554), a failure with
        # cancel_others_on_failure=False does NOT cancel downstream
        # dependents — independent branches continue deploying and only
        # cancel_others_on_failure=True cancels everything.
        self.assertEqual(res["status"], "released")
        self.assertEqual(res["cancelled_dependents"], 0)
        mock_cancel_dep.assert_not_called()


class TestWaveTimeoutOrphanFixes(TestCase):
    """Timed-out in-progress deployments and orphaned deferred builds must
    reach a terminal state instead of lingering forever with no owner."""

    @patch("apps.deployments.tasks.ecosystem.tasks._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks.ecosystem.tasks._cancel_unreleased_deployments")
    @patch("apps.deployments.models.Deployment.objects.bulk_update")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.tasks._rebuild_ecosystem_build_counter")
    def test_wave_timeout_marks_hung_in_progress_cancelled(
        self, mock_counter, mock_dep_filter, mock_bulk_update,
        mock_cancel_unreleased, mock_finalize,
    ):
        """A BUILDING row the wave task gives up waiting on becomes CANCELLED."""
        values_mock = MagicMock()
        values_mock.values.return_value = [
            {"id": "dep-1", "status": Deployment.Status.BUILDING}
        ]
        hung = MagicMock(status=Deployment.Status.BUILDING, build_logs="")
        mock_dep_filter.side_effect = [values_mock, [hung]]
        mock_cancel_unreleased.return_value = 0

        waves = [["dep-1"], ["dep-2"]]
        res = ecosystem_release_wave_task.run(
            provider_id="prov-1",
            waves=waves,
            wave_index=1,
            recheck_count=10,
            max_rechecks=10,
            plan_id="plan-123",
        )

        self.assertEqual(res["status"], "timed_out")
        self.assertEqual(hung.status, Deployment.Status.CANCELLED)
        self.assertIsNotNone(hung.finished_at)
        self.assertIn("timed out", hung.build_logs)
        mock_bulk_update.assert_called_once()

    @patch("apps.deployments.tasks.ecosystem.tasks._finalize_ecosystem_plan")
    @patch("apps.deployments.tasks.ecosystem.tasks._cancel_unreleased_deployments")
    @patch("apps.deployments.models.Deployment.objects.bulk_update")
    @patch("apps.deployments.models.Deployment.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.tasks._rebuild_ecosystem_build_counter")
    def test_wave_timeout_skips_row_finished_during_timeout(
        self, mock_counter, mock_dep_filter, mock_bulk_update,
        mock_cancel_unreleased, mock_finalize,
    ):
        """A row that reached ACTIVE between the status read and the timeout
        write must NOT be clobbered back to CANCELLED."""
        values_mock = MagicMock()
        values_mock.values.return_value = [
            {"id": "dep-1", "status": Deployment.Status.BUILDING}
        ]
        finished = MagicMock(status=Deployment.Status.ACTIVE, build_logs="")
        mock_dep_filter.side_effect = [values_mock, [finished]]
        mock_cancel_unreleased.return_value = 0

        waves = [["dep-1"], ["dep-2"]]
        res = ecosystem_release_wave_task.run(
            provider_id="prov-1",
            waves=waves,
            wave_index=1,
            recheck_count=10,
            max_rechecks=10,
            plan_id="plan-123",
        )

        self.assertEqual(res["status"], "timed_out")
        self.assertEqual(finished.status, Deployment.Status.ACTIVE)
        mock_bulk_update.assert_not_called()

    @patch.object(ecosystem_deferred_build_task.app, "send_task")
    @patch("apps.deployments.tasks.ecosystem.tasks._ecosystem_plan_still_deploying")
    @patch("apps.deployments.tasks.ecosystem.tasks._count_active_ecosystem_builds")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_deferred_build_orphan_cancelled_when_plan_finished(
        self, mock_dep_filter, mock_count, mock_plan_alive, mock_send_task
    ):
        """A deferred build whose plan already finalized is CANCELLED, not re-queued."""
        deployment = MagicMock(status=Deployment.Status.QUEUED, build_logs="")
        mock_dep_filter.return_value.first.return_value = deployment
        mock_count.return_value = 99
        mock_plan_alive.return_value = False

        res = ecosystem_deferred_build_task.run(
            deployment_id="dep-1", provider_id="prov-1",
            wave_index=0, plan_id="plan-123",
        )

        self.assertEqual(res["status"], "cancelled")
        self.assertEqual(deployment.status, Deployment.Status.CANCELLED)
        mock_send_task.assert_not_called()
        deployment.save.assert_called_once()

    @patch.object(ecosystem_deferred_build_task.app, "send_task")
    @patch("apps.deployments.tasks.ecosystem.tasks._ecosystem_plan_still_deploying")
    @patch("apps.deployments.tasks.ecosystem.tasks._count_active_ecosystem_builds")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_deferred_build_keeps_deferring_while_plan_alive(
        self, mock_dep_filter, mock_count, mock_plan_alive, mock_send_task
    ):
        """While the plan is still deploying, the deferred build re-queues with its plan_id."""
        deployment = MagicMock(status=Deployment.Status.QUEUED, build_logs="")
        mock_dep_filter.return_value.first.return_value = deployment
        mock_count.return_value = 99
        mock_plan_alive.return_value = True

        res = ecosystem_deferred_build_task.run(
            deployment_id="dep-1", provider_id="prov-1",
            wave_index=0, plan_id="plan-123",
        )

        self.assertEqual(res["status"], "deferred")
        self.assertEqual(deployment.status, Deployment.Status.QUEUED)
        mock_send_task.assert_called_once()
        sent_kwargs = mock_send_task.call_args[1]
        self.assertIn("plan-123", sent_kwargs["args"])

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_plan_liveness_deploying(self, mock_plan_filter):
        mock_plan_filter.return_value.values_list.return_value.first.return_value = "deploying"
        self.assertTrue(_ecosystem_plan_still_deploying("plan-123"))

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_plan_liveness_missing_plan_counts_as_finished(self, mock_plan_filter):
        mock_plan_filter.return_value.values_list.return_value.first.return_value = None
        self.assertFalse(_ecosystem_plan_still_deploying("plan-123"))

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    def test_plan_liveness_db_error_fails_open(self, mock_plan_filter):
        mock_plan_filter.side_effect = Exception("db down")
        self.assertTrue(_ecosystem_plan_still_deploying("plan-123"))


class TestPreEcosystemSnapshot(TestCase):
    """Reused services get a PRE_DEPLOY snapshot; new/failed ones don't."""

    def _filter_mock(self, exists=True, raises=False):
        mock_filter = MagicMock()
        if raises:
            mock_filter.return_value.exclude.side_effect = Exception("db down")
        else:
            mock_filter.return_value.exclude.return_value.exists.return_value = exists
        return mock_filter

    @patch("apps.deployments.services.snapshot_service.SnapshotService")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_reused_service_gets_snapshot(self, mock_dep_filter, mock_snap_cls):
        mock_dep_filter.return_value = self._filter_mock(exists=True).return_value
        mock_snap_cls.capture_snapshot.return_value = MagicMock(id="snap-1")
        service = MagicMock(); service.id = 'svc-1'; service.name = 'api'

        res = _capture_pre_ecosystem_snapshot(service, "dep-9", "plan-123", MagicMock())

        self.assertEqual(res, "snap-1")
        mock_snap_cls.capture_snapshot.assert_called_once()
        _, kwargs = mock_snap_cls.capture_snapshot.call_args
        self.assertEqual(kwargs["trigger"], "PRE_DEPLOY")
        self.assertEqual(kwargs["service_id"], "svc-1")
        self.assertIn("plan-123", kwargs["label"][:60])

    @patch("apps.deployments.services.snapshot_service.SnapshotService")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_brand_new_service_skipped(self, mock_dep_filter, mock_snap_cls):
        mock_dep_filter.return_value = self._filter_mock(exists=False).return_value
        service = MagicMock(); service.id = 'svc-1'; service.name = 'api'

        res = _capture_pre_ecosystem_snapshot(service, "dep-9", "plan-123", MagicMock())

        self.assertIsNone(res)
        mock_snap_cls.capture_snapshot.assert_not_called()

    @patch("apps.deployments.services.snapshot_service.SnapshotService")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_snapshot_failure_is_non_fatal(self, mock_dep_filter, mock_snap_cls):
        mock_dep_filter.return_value = self._filter_mock(exists=True).return_value
        mock_snap_cls.capture_snapshot.side_effect = Exception("pg clone failed")
        service = MagicMock(); service.id = 'svc-1'; service.name = 'api'

        res = _capture_pre_ecosystem_snapshot(service, "dep-9", "plan-123", MagicMock())

        self.assertIsNone(res)



