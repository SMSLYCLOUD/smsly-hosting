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
    _fail_plan_record,
    _finalize_ecosystem_plan,
    _another_deploy_attempt_running,
    _ensure_valid_fernet_env_value,
    _unknown_plan_service_ref,
    _rollback_ecosystem_deploy,
    ecosystem_deferred_build_task,
    ecosystem_deploy_task,
    ecosystem_release_wave_task,
)
from apps.deployments.tasks.ecosystem.helpers.env_vars import (
    _resolve_env_placeholders,
    _service_placeholder_url,
)
from apps.deployments.tasks.ecosystem.helpers.lifecycle import (
    _count_active_ecosystem_builds,
    _queue_wave,
    _rebuild_ecosystem_build_counter,
)


class TestEcosystemRobustFixes(TestCase):
    """Test suite for robust wave orchestration and EcosystemPlan finalization."""

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_completed(self, mock_dep_filter, mock_plan_filter):
        """When all deployments in all waves succeed, plan status transitions to COMPLETED."""
        mock_plan = MagicMock()
        mock_plan.services_status = {}
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.ACTIVE, "service__name": "api-1"},
            {"status": Deployment.Status.ACTIVE, "service__name": "api-2"},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        self.assertEqual(mock_plan.status, EcosystemPlan.Status.COMPLETED)
        self.assertEqual(mock_plan.error_message, "")
        self.assertEqual(mock_plan.services_status.get("api-1"), Deployment.Status.ACTIVE)
        self.assertEqual(mock_plan.services_status.get("api-2"), Deployment.Status.ACTIVE)
        mock_plan.save.assert_called_once()

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_failed(self, mock_dep_filter, mock_plan_filter):
        """When any deployment fails, plan status transitions to FAILED."""
        mock_plan = MagicMock()
        mock_plan.services_status = {}
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.ACTIVE, "service__name": "api-1"},
            {"status": Deployment.Status.FAILED, "service__name": "api-2"},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        self.assertEqual(mock_plan.status, EcosystemPlan.Status.FAILED)
        self.assertIn("service failures or cancellations", mock_plan.error_message)
        self.assertEqual(mock_plan.services_status.get("api-1"), Deployment.Status.ACTIVE)
        self.assertEqual(mock_plan.services_status.get("api-2"), Deployment.Status.FAILED)
        mock_plan.save.assert_called_once()

    @patch("apps.deployments.models.ecosystem.EcosystemPlan.objects.filter")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_finalize_ecosystem_plan_persists_services_status_in_progress(self, mock_dep_filter, mock_plan_filter):
        """In-progress deployments persist services_status so the UI can show partial state."""
        mock_plan = MagicMock()
        mock_plan.services_status = {}
        mock_plan_filter.return_value.first.return_value = mock_plan

        mock_dep_filter.return_value.values.return_value = [
            {"status": Deployment.Status.BUILDING, "service__name": "api-1"},
            {"status": Deployment.Status.QUEUED, "service__name": "api-2"},
        ]

        _finalize_ecosystem_plan("plan-123", [["dep-1"], ["dep-2"]])

        self.assertEqual(mock_plan.services_status.get("api-1"), Deployment.Status.BUILDING)
        self.assertEqual(mock_plan.services_status.get("api-2"), Deployment.Status.QUEUED)
        mock_plan.save.assert_called_once()

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

        # A dependency failure cancels only its transitive dependents;
        # independent branches continue deploying.
        self.assertEqual(res["status"], "released")
        self.assertEqual(res["cancelled_dependents"], 1)
        mock_cancel_dep.assert_called_once()


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


class TestActiveBuildCounting(TestCase):
    """The concurrency gate must count running builds, never the waiting queue."""

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_queued_rows_do_not_consume_slots(self, mock_filter):
        mock_filter.return_value.count.return_value = 0
        self.assertEqual(_count_active_ecosystem_builds(), 0)
        kwargs = mock_filter.call_args[1]
        self.assertNotIn(Deployment.Status.QUEUED, set(kwargs["status__in"]))

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_dispatched_and_running_states_count(self, mock_filter):
        mock_filter.return_value.count.return_value = 3
        self.assertEqual(_count_active_ecosystem_builds(), 3)
        swept = set(mock_filter.call_args[1]["status__in"])
        self.assertIn(Deployment.Status.REVIEW, swept)
        self.assertIn(Deployment.Status.BUILDING, swept)
        self.assertIn(Deployment.Status.DEPLOYING, swept)
        self.assertIn(Deployment.Status.HEALTH_CHECK, swept)

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_stale_rows_do_not_consume_slots(self, mock_filter):
        mock_filter.return_value.count.return_value = 0
        _count_active_ecosystem_builds()
        self.assertIn("updated_at__gte", mock_filter.call_args[1])

    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_count_db_error_fails_open_to_zero(self, mock_filter):
        mock_filter.side_effect = Exception("db down")
        self.assertEqual(_count_active_ecosystem_builds(), 0)

    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle.django_cache")
    @patch("apps.deployments.models.Deployment.objects.filter")
    def test_rebuild_counter_matches_count_query(self, mock_filter, mock_cache):
        mock_filter.return_value.count.return_value = 2
        _rebuild_ecosystem_build_counter()
        kwargs = mock_filter.call_args[1]
        self.assertNotIn(Deployment.Status.QUEUED, set(kwargs["status__in"]))
        mock_cache.set.assert_called_once()
        self.assertEqual(mock_cache.set.call_args[0][1], 2)


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


class TestEcosystemRollback(TestCase):
    """Verify _rollback_ecosystem_deploy removes only the created resources
    and never touches live/unrelated rows."""

    @patch("apps.deployments.models.network_scope.ScopedNetwork.objects.filter")
    @patch("apps.deployments.models.addons.Addon.objects.filter")
    @patch("apps.deployments.models.environment.EnvironmentVariable.objects.filter")
    @patch("apps.deployments.models.Service.objects.filter")
    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle._rebuild_ecosystem_build_counter")
    @patch("apps.deployments.models.deployment.Deployment.objects.filter")
    def test_rollback_removes_created_resources_only(
        self,
        mock_dep_filter, mock_counter, mock_svc_filter, mock_env_filter,
        mock_addon_filter, mock_scoped_filter,
    ):
        """Created rows are removed; live/unrelated rows are not."""
        from apps.deployments.models.addons import Addon

        def _q(*args, **kwargs):
            m = MagicMock()
            m.exclude.return_value = m
            m.delete.return_value = None
            return m

        mock_dep_filter.return_value = _q()
        mock_svc_filter.return_value = _q()
        mock_addon_filter.return_value = _q()
        mock_env_filter.return_value = _q()
        mock_scoped_filter.return_value = _q()

        _rollback_ecosystem_deploy(
            service_ids=["svc-1"],
            deployment_ids=["dep-1"],
            addon_ids=["ad-1"],
            env_var_keys=["API_KEY"],
            project_id="proj-1",
        )

        # Live deployments/addons are excluded by status; only created rows are removed.
        # Service/EnvVar/ScopedNet may be queried twice (once for status filter,
        # once for project scope) so allow any positive call count.
        self.assertGreaterEqual(mock_dep_filter.call_count, 1)
        self.assertGreaterEqual(mock_addon_filter.call_count, 1)
        self.assertGreaterEqual(mock_env_filter.call_count, 1)
        self.assertGreaterEqual(mock_svc_filter.call_count, 1)
        self.assertGreaterEqual(mock_scoped_filter.call_count, 1)

    def test_rollback_handles_empty_input(self):
        """Idempotent when no resources were tracked."""
        _rollback_ecosystem_deploy(
            service_ids=[],
            deployment_ids=[],
            addon_ids=[],
            env_var_keys=[],
        )


class TestEcosystemTaskLiveness(TestCase):
    """_another_deploy_attempt_running: only a DIFFERENT live attempt trips
    the guard — our own re-dispatch must fall through to recovery."""

    def _task_self(self, me="me-1", active=None, reserved=None):
        task_self = MagicMock()
        task_self.request.id = me
        task_self.app.control.inspect.return_value.active.return_value = active
        task_self.app.control.inspect.return_value.reserved.return_value = reserved
        return task_self

    def _entry(self, tid, plan_id):
        return {
            "id": tid,
            "name": "apps.deployments.tasks_ecosystem.ecosystem_deploy_task",
            "args": ["user-1", {}, plan_id],
            "kwargs": {"plan_id": plan_id},
        }

    def test_no_other_attempt_is_dead(self):
        task_self = self._task_self(active={}, reserved={})
        self.assertFalse(_another_deploy_attempt_running(task_self, "plan-1"))

    def test_only_self_running_is_dead(self):
        mine = self._entry("me-1", "plan-1")
        task_self = self._task_self(active={"w@h": [mine]}, reserved={})
        self.assertFalse(_another_deploy_attempt_running(task_self, "plan-1"))

    def test_other_attempt_same_plan_is_live(self):
        other = self._entry("other-9", "plan-1")
        task_self = self._task_self(active={"w@h": [other]}, reserved={})
        self.assertTrue(_another_deploy_attempt_running(task_self, "plan-1"))

    def test_other_attempt_other_plan_is_dead(self):
        other = self._entry("other-9", "plan-2")
        task_self = self._task_self(active={"w@h": [other]}, reserved={})
        self.assertFalse(_another_deploy_attempt_running(task_self, "plan-1"))

    def test_reserved_other_attempt_is_live(self):
        other = self._entry("other-9", "plan-1")
        task_self = self._task_self(active={}, reserved={"w@h": [other]})
        self.assertTrue(_another_deploy_attempt_running(task_self, "plan-1"))

    def test_inspect_failure_is_dead(self):
        task_self = self._task_self()
        task_self.app.control.inspect.side_effect = Exception("broker down")
        self.assertFalse(_another_deploy_attempt_running(task_self, "plan-1"))


class TestEcosystemGuardRecovery(TestCase):
    """Idempotency guard: dead attempts fall through to creation recovery.

    Regression (2026-09-07): 10 QUEUED rows with no live task first fell
    through the in-flight branch, then hit the all-terminal check whose
    else returned already_deployed — stranding the plan with no driver.
    The validation error below is intentional: it proves the task got
    PAST the guard into the creation path.
    """

    def _run_guard(self, active_exists, other_live, failed_exists):
        existing_plan = MagicMock()
        existing_plan.project = MagicMock()
        qs = MagicMock()
        qs.exists.return_value = True
        active_qs = MagicMock()
        active_qs.exists.return_value = active_exists
        active_qs.count.return_value = 10
        failed_qs = MagicMock()
        failed_qs.exists.return_value = failed_exists
        failed_qs.count.return_value = 4

        def _filter(**kwargs):
            if "status__in" in kwargs:
                return failed_qs
            return qs

        qs.filter.side_effect = _filter
        qs.exclude.return_value = active_qs
        with patch(
            "apps.mtls.views.ensure_ecosystem_spire", return_value="ok"
        ), patch(
            "django.contrib.auth.get_user_model"
        ) as mock_user_model, patch(
            "apps.deployments.models.EcosystemPlan"
        ) as mock_plan_model, patch(
            "apps.deployments.tasks.ecosystem.tasks.Deployment"
        ) as mock_dep_model, patch(
            "apps.deployments.tasks.ecosystem.tasks._another_deploy_attempt_running",
            return_value=other_live,
        ), patch(
            "apps.deployments.tasks.ecosystem.tasks._validate_plan_structure",
            return_value=["boom"],
        ):
            mock_plan_model.objects.get.return_value = existing_plan
            mock_user_model.return_value.objects.get.return_value = MagicMock(
                id="user-1"
            )
            mock_dep_model.objects.filter.return_value = qs
            mock_dep_model.Status = Deployment.Status
            return ecosystem_deploy_task.run(
                "user-1", {"services": []},
                plan_id="plan-1", project_id="proj-1",
            )

    def test_live_attempt_returns_in_progress(self):
        res = self._run_guard(
            active_exists=True, other_live=True, failed_exists=False,
        )
        self.assertEqual(res["status"], "already_in_progress")

    def test_dead_attempt_falls_through_to_creation(self):
        res = self._run_guard(
            active_exists=True, other_live=False, failed_exists=False,
        )
        # Past the guard → stopped at plan-structure validation.
        self.assertEqual(res["error"], "Plan validation failed")

    def test_all_terminal_reruns_creation(self):
        res = self._run_guard(
            active_exists=False, other_live=False, failed_exists=True,
        )
        self.assertEqual(res["error"], "Plan validation failed")

    def test_no_failed_no_active_returns_deployed(self):
        res = self._run_guard(
            active_exists=False, other_live=False, failed_exists=False,
        )
        self.assertEqual(res["status"], "already_deployed")


class TestFernetEnvRepair(TestCase):
    """_ensure_valid_fernet_env_value: invalid crypto keys are regenerated.

    Regression (2026-09-07): a 50-char FIELD_ENCRYPTION_KEY crashed the
    backend container at boot (Fernet key must be 32 url-safe b64 bytes).
    """

    def test_valid_fernet_key_passes_through(self):
        from cryptography.fernet import Fernet

        good = Fernet.generate_key().decode()
        self.assertEqual(
            _ensure_valid_fernet_env_value("FIELD_ENCRYPTION_KEY", good), good
        )
        self.assertEqual(_ensure_valid_fernet_env_value("FERNET_KEY", good), good)

    def test_invalid_fernet_key_is_regenerated(self):
        from cryptography.fernet import Fernet

        fixed = _ensure_valid_fernet_env_value(
            "FIELD_ENCRYPTION_KEY", "x" * 50,
        )
        self.assertNotEqual(fixed, "x" * 50)
        Fernet(fixed.encode())  # must not raise
        fixed2 = _ensure_valid_fernet_env_value("BACKUP_ENCRYPTION_KEY", "nope")
        Fernet(fixed2.encode())  # must not raise

    def test_non_crypto_keys_untouched(self):
        self.assertEqual(
            _ensure_valid_fernet_env_value("SECRET_KEY", "x" * 50), "x" * 50
        )
        self.assertEqual(
            _ensure_valid_fernet_env_value("DATABASE_URL", "postgres://x"),
            "postgres://x",
        )


class TestUnknownPlanServiceRef(TestCase):
    """_unknown_plan_service_ref: only forward refs to unprepared plan
    entries qualify for the creation retry (2026-09-08)."""

    def _entries(self):
        return {
            "smslycloud/smsly-frontend": {
                "repo": "smslycloud/smsly-frontend",
                "name": "smsly-frontend",
                "requested_name": "smsly-frontend",
            },
            "smslycloud/smsly-backend": {
                "repo": "smslycloud/smsly-backend",
                "name": "smsly-backend",
                "requested_name": "smsly-backend",
            },
        }

    def test_forward_ref_to_unprepared_entry_matches(self):
        ref = _unknown_plan_service_ref(
            "Service placeholder references unknown service 'smsly-frontend'. "
            "Declare it in the ecosystem plan before deployment.",
            self._entries(),
            {"smslycloud/smsly-backend": "dep-1"},
        )
        self.assertEqual(ref, "smsly-frontend")

    def test_ref_to_prepared_entry_is_none(self):
        ref = _unknown_plan_service_ref(
            "Service placeholder references unknown service 'smsly-backend'. Declare it.",
            self._entries(),
            {"smslycloud/smsly-backend": "dep-1",
             "smslycloud/smsly-frontend": "dep-2"},
        )
        self.assertIsNone(ref)

    def test_genuinely_unknown_ref_is_none(self):
        ref = _unknown_plan_service_ref(
            "Service placeholder references unknown service 'nope'. Declare it.",
            self._entries(),
            {},
        )
        self.assertIsNone(ref)

    def test_unrelated_error_is_none(self):
        ref = _unknown_plan_service_ref("boom", self._entries(), {})
        self.assertIsNone(ref)


class TestInternalServiceUrls(TestCase):
    """Internal {{SERVICE:x}} refs resolve to mTLS HTTPS; others to HTTP."""

    def _created(self):
        svc = MagicMock()
        svc.name = "smsly-backend"
        svc.internal_port = 8080
        return {"smsly-backend": svc}

    def test_internal_target_uses_mtls_https(self):
        url = _service_placeholder_url(
            "smsly-backend", self._created(),
            internal_names={"smsly-backend"},
        )
        self.assertEqual(url, "https://smsly-backend:80")

    def test_internal_authority_uses_sidecar_port(self):
        url = _service_placeholder_url(
            "smsly-backend", self._created(),
            as_authority=True, internal_names={"smsly-backend"},
        )
        self.assertEqual(url, "smsly-backend:80")

    def test_external_target_uses_plain_http(self):
        url = _service_placeholder_url(
            "smsly-backend", self._created(), internal_names=set()
        )
        self.assertEqual(url, "http://smsly-backend:8080")

    def test_legacy_default_stays_http(self):
        url = _service_placeholder_url("smsly-backend", self._created())
        self.assertEqual(url, "http://smsly-backend:8080")

    def test_resolve_env_mixes_schemes(self):
        created = self._created()
        fe = MagicMock()
        fe.name = "smsly-frontend"
        fe.internal_port = 3000
        created["smsly-frontend"] = fe
        out = _resolve_env_placeholders(
            {
                "API_URL": "{{SERVICE:smsly-backend}}",
                "FE_URL": "{{SERVICE:smsly-frontend}}",
            },
            created,
            internal_names={"smsly-backend"},
        )
        self.assertEqual(out["API_URL"], "https://smsly-backend:80")
        self.assertEqual(out["FE_URL"], "http://smsly-frontend:3000")


class TestWaitSidecarReady(TestCase):
    """wait_sidecar_ready gates go-live on admin + issued SVID."""

    def _service(self):
        svc = MagicMock()
        svc.name = "smsly-backend"
        return svc

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_name",
           return_value="envoy-smsly-backend")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_ready_when_admin_up_and_svid_present(self, mock_client_fn, _mock_name):
        from apps.mtls.services.envoy_sidecar import EnvoySidecar

        container = MagicMock()
        container.status = "running"
        ready = MagicMock(exit_code=0, output=b"OK")
        certs = MagicMock(
            exit_code=0,
            output=b'{"certificates": [{"cert_chain": [{"subject_alt_names": '
                   b'[{"uri": "spiffe://ecosystem.local/service/smsly-backend"}]}]}]}',
        )
        container.exec_run.side_effect = [ready, certs]
        mock_client_fn.return_value.containers.get.return_value = container

        self.assertTrue(EnvoySidecar.wait_sidecar_ready(self._service(), timeout_seconds=30))
        self.assertEqual(container.exec_run.call_count, 2)

    @patch("apps.mtls.services.envoy_sidecar.EnvoySidecar.get_sidecar_name",
           return_value="envoy-smsly-backend")
    @patch("apps.cloud.docker_client.get_docker_client")
    @patch("apps.mtls.services.envoy_sidecar.time")
    def test_false_when_never_ready(self, mock_time, mock_client_fn, _mock_name):
        from apps.mtls.services.envoy_sidecar import EnvoySidecar

        container = MagicMock()
        container.status = "running"
        container.exec_run.return_value = MagicMock(exit_code=1, output=b"")
        mock_client_fn.return_value.containers.get.return_value = container
        mock_time.time.side_effect = [0, 0, 0, 100]

        self.assertFalse(EnvoySidecar.wait_sidecar_ready(self._service(), timeout_seconds=10))


class TestQueueWaveDispatch(TestCase):
    """_queue_wave must flip QUEUED→REVIEW with a Postgres-safe update."""

    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle._increment_active_ecosystem_builds")
    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle._count_active_ecosystem_builds", return_value=0)
    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle._has_enough_memory", return_value=True)
    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle._get_ecosystem_build_config")
    @patch("apps.deployments.tasks.ecosystem.helpers.lifecycle.Deployment")
    def test_queue_wave_dispatches_with_concat_update(
        self, mock_dep, mock_cfg, mock_mem, mock_count, mock_incr,
    ):
        from django.db.models.functions import Concat

        mock_cfg.return_value = {
            "max_concurrent_builds": 5,
            "build_stagger_seconds": 10,
        }
        deployment = MagicMock()
        deployment.id = "dep-1"
        deployment.status = Deployment.Status.QUEUED
        mock_dep.objects.filter.return_value.first.return_value = deployment
        mock_dep.objects.filter.return_value.update.return_value = 1
        mock_dep.Status = Deployment.Status

        app = MagicMock()
        queued = _queue_wave(app, ["dep-1"], "prov-1", 0, plan_id="plan-1")

        self.assertEqual(queued, 1)
        _, kwargs = mock_dep.objects.filter.return_value.update.call_args
        self.assertEqual(kwargs["status"], Deployment.Status.REVIEW)
        # Regression (2026-09-07): F("build_logs") + str raises
        # ProgrammingError on Postgres (no text + unknown operator).
        self.assertIsInstance(kwargs["build_logs"], Concat)
        app.send_task.assert_called_once()
        send_args, send_kwargs = app.send_task.call_args
        self.assertIn("smart_deploy_task", send_args[0])



