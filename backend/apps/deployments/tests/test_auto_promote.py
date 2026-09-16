"""Unit tests for auto-promote/auto-review sweeps (ORM fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

import docker

from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments

_READY = {'ready': True, 'blockers': [], 'warnings': [], 'policy': {}}


class TestAutoPromoteStagedDeployments(TestCase):
    def _run(self, rows, readiness=None):
        mgr = MagicMock()
        mgr.filter.return_value.select_related.return_value = rows
        with patch("apps.deployments.models.Deployment.objects", mgr), \
              patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12), \
              patch("apps.deployments.tasks.deploy.promote.append_log") as mock_log, \
              patch("apps.deployments.services.safedeploy.promotion_guard.check_promotion_readiness",
                    return_value=readiness or dict(_READY)) as mock_ready, \
              patch("apps.deployments.tasks.deploy.promote._do_promote") as mock_promote:
            return auto_promote_staged_deployments.run(), mock_promote, mock_ready, mock_log

    def test_promotes_eligible_rows(self):
        row = MagicMock()
        res, mock_promote, _, _ = self._run([row])
        self.assertEqual(res, {"promoted": 1, "skipped": 0})
        mock_promote.assert_called_once()

    def test_empty_sweep_returns_zero(self):
        res, mock_promote, _, _ = self._run([])
        self.assertEqual(res, {"promoted": 0, "skipped": 0})
        mock_promote.assert_not_called()

    def test_not_ready_rows_stay_staged(self):
        """Readiness blockers skip (not fail) the row for the next sweep."""
        from apps.deployments.models import Deployment as RealDeployment
        row = MagicMock()
        row.status = RealDeployment.Status.STAGED
        res, mock_promote, _, mock_log = self._run(
            [row],
            readiness={'ready': False, 'blockers': ['Soak time not met'], 'warnings': [], 'policy': {}},
        )
        self.assertEqual(res, {"promoted": 0, "skipped": 1})
        mock_promote.assert_not_called()
        self.assertEqual(row.status, RealDeployment.Status.STAGED)
        row.save.assert_not_called()
        mock_log.assert_called_once()

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
              patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12), \
              patch("apps.deployments.services.safedeploy.promotion_guard.check_promotion_readiness",
                    return_value=dict(_READY)):
            from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments as task
            res = task.run()
        self.assertEqual(res, {"promoted": 0, "skipped": 0})
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
              patch("apps.deployments.tasks.deploy.promote._get_config_hours", return_value=12), \
              patch("apps.deployments.services.safedeploy.promotion_guard.check_promotion_readiness",
                    return_value=dict(_READY)):
            from apps.deployments.tasks.deploy.promote import auto_promote_staged_deployments as task
            res = task.run()
        self.assertEqual(res, {"promoted": 0, "skipped": 0})
        self.assertEqual(row.status, RealDeployment.Status.STAGED)
        row.save.assert_not_called()


def _staged_row(green_id="green123"):
    from apps.deployments.models import Deployment as RealDeployment
    row = MagicMock()
    row.id = "dep-1"
    row.status = RealDeployment.Status.STAGED
    row.green_container_id = green_id
    return row


class TestReapUnhealthyStagedDeployments(TestCase):
    def _run(self, rows, container=None, get_side_effect=None):
        from apps.deployments.tasks.deploy.promote import (
            reap_unhealthy_staged_deployments as task,
        )
        mgr = MagicMock()
        mgr.filter.return_value.select_related.return_value.__getitem__.return_value = rows
        client = MagicMock()
        if get_side_effect is not None:
            client.containers.get.side_effect = get_side_effect
        else:
            client.containers.get.return_value = container
        with patch("apps.deployments.models.Deployment.objects", mgr), \
             patch("apps.deployments.tasks.deploy.promote.docker.from_env", return_value=client), \
             patch("apps.deployments.tasks.deploy.promote.append_log"), \
             patch("apps.deployments.tasks.deploy.promote.broadcast_status"):
            return task.run(), client

    def _container(self, status="running", health="starting"):
        c = MagicMock()
        c.attrs = {"State": {"Status": status, "Health": {"Status": health}}}
        return c

    def test_missing_green_fails_row(self):
        res, _ = self._run(
            [_staged_row()], get_side_effect=docker.errors.NotFound("gone")
        )
        self.assertEqual(res, {"reaped": 1})

    def test_empty_green_id_fails_row(self):
        res, _ = self._run([_staged_row(green_id="")])
        self.assertEqual(res, {"reaped": 1})

    def test_exited_green_reaped_and_removed(self):
        container = self._container(status="exited", health="")
        res, client = self._run([_staged_row()], container=container)
        self.assertEqual(res, {"reaped": 1})
        container.remove.assert_called_once_with(force=True)

    def test_unhealthy_green_reaped(self):
        container = self._container(status="running", health="unhealthy")
        res, _ = self._run([_staged_row()], container=container)
        self.assertEqual(res, {"reaped": 1})

    def test_stuck_starting_green_reaped(self):
        """2026-09-14 policy-service: health=starting for 9h."""
        container = self._container(status="running", health="starting")
        res, _ = self._run([_staged_row()], container=container)
        self.assertEqual(res, {"reaped": 1})

    def test_healthy_green_left_alone(self):
        container = self._container(status="running", health="healthy")
        res, _ = self._run([_staged_row()], container=container)
        self.assertEqual(res, {"reaped": 0})
        container.remove.assert_not_called()

    def test_running_without_healthcheck_left_alone(self):
        container = self._container(status="running", health="")
        res, _ = self._run([_staged_row()], container=container)
        self.assertEqual(res, {"reaped": 0})

    def test_empty_sweep(self):
        res, _ = self._run([])
        self.assertEqual(res, {"reaped": 0})

    def test_canary_healthy_green_never_reaped(self):
        """Canary-active HEALTHY greens serve weighted prod traffic — exempt."""
        from types import SimpleNamespace

        row = SimpleNamespace(
            id="dep-9",
            status="STAGED",
            green_container_id="green123",
            staged_at=None,
            service=SimpleNamespace(
                deploy_strategy="CANARY", canary_percentage=25,
            ),
        )
        res, client = self._run([row], container=self._container(status="running", health="healthy"))
        self.assertEqual(res, {"reaped": 0})
        client.containers.get.assert_called_once()
        client.containers.get.return_value.remove.assert_not_called()

    def test_canary_dead_green_still_reaped(self):
        """A canary split to a dead backend 502s traffic — reap it anyway."""
        from types import SimpleNamespace

        row = SimpleNamespace(
            id="dep-9",
            status="STAGED",
            green_container_id="green123",
            staged_at=None,
            service=SimpleNamespace(
                deploy_strategy="CANARY", canary_percentage=25,
            ),
        )
        res, _ = self._run([row], container=self._container(status="exited", health=""))
        self.assertEqual(res, {"reaped": 1})


class TestPromotionGuard(TestCase):
    def _deployment(self, **kwargs):
        from datetime import timedelta
        from types import SimpleNamespace

        from django.utils import timezone

        base = dict(
            id="dep-1",
            status="STAGED",
            green_container_id="green123",
            staged_at=timezone.now() - timedelta(hours=1),
            commit_hash="abc123",
            service=SimpleNamespace(
                id="svc-1",
                deploy_strategy="ROLLING",
                canary_percentage=0,
                promotion_policy={},
            ),
        )
        base.update(kwargs)
        return SimpleNamespace(**base)

    def _ready_guard(self, deployment, provider=None, **patches):
        defaults = dict(
            _green_container_state=("running", "healthy"),
            _validation_for_deployment=None,
            _approval_exists=True,
        )
        defaults.update(patches)
        mods = []
        for name, ret in defaults.items():
            if name == "_green_container_state":
                m = patch(
                    "apps.deployments.services.safedeploy.promotion_guard._green_container_state",
                    return_value=ret,
                )
            elif name == "_validation_for_deployment":
                m = patch(
                    "apps.deployments.services.safedeploy.promotion_guard._validation_for_deployment",
                    return_value=ret,
                )
            else:
                m = patch(
                    "apps.deployments.services.safedeploy.promotion_guard._approval_exists",
                    return_value=ret,
                )
            mods.append(m)
        from apps.deployments.services.safedeploy.promotion_guard import (
            check_promotion_readiness,
        )
        for m in mods:
            m.start()
        try:
            return check_promotion_readiness(deployment, provider=provider)
        finally:
            for m in mods:
                m.stop()

    def test_ready_when_healthy_and_soaked(self):
        res = self._ready_guard(self._deployment())
        self.assertTrue(res["ready"])
        self.assertEqual(res["blockers"], [])

    def test_non_staged_blocked(self):
        res = self._ready_guard(self._deployment(status="BUILDING"))
        self.assertFalse(res["ready"])

    def test_missing_green_blocked(self):
        res = self._ready_guard(self._deployment(green_container_id=""))
        self.assertFalse(res["ready"])

    def test_unhealthy_green_blocked_by_default(self):
        res = self._ready_guard(
            self._deployment(), _green_container_state=("running", "unhealthy")
        )
        self.assertFalse(res["ready"])
        self.assertTrue(any("Green container" in b for b in res["blockers"]))

    def test_unhealthy_green_warns_when_requirement_off(self):
        dep = self._deployment()
        dep.service.promotion_policy = {"require_green_healthy": False}
        res = self._ready_guard(
            dep, _green_container_state=("exited", "")
        )
        self.assertTrue(res["ready"])
        self.assertTrue(res["warnings"])

    def test_soak_time_blocks_until_met(self):
        from datetime import timedelta
        from django.utils import timezone

        dep = self._deployment(staged_at=timezone.now() - timedelta(seconds=10))
        dep.service.promotion_policy = {"min_staging_seconds": 3600}
        res = self._ready_guard(dep)
        self.assertFalse(res["ready"])
        self.assertTrue(any("Soak time" in b for b in res["blockers"]))

    def test_failed_validation_blocks_only_when_required(self):
        from types import SimpleNamespace

        validation = SimpleNamespace(status="FAILED", risk_level="MEDIUM")
        res = self._ready_guard(
            self._deployment(), _validation_for_deployment=validation
        )
        self.assertTrue(res["ready"])  # warn-only by default
        self.assertTrue(res["warnings"])
        dep = self._deployment()
        dep.service.promotion_policy = {"require_migration_passed": True}
        res = self._ready_guard(dep, _validation_for_deployment=validation)
        self.assertFalse(res["ready"])

    def test_high_risk_without_approval_blocked(self):
        from types import SimpleNamespace

        validation = SimpleNamespace(status="PASSED", risk_level="HIGH")
        res = self._ready_guard(
            self._deployment(), _validation_for_deployment=validation,
            _approval_exists=False,
        )
        self.assertFalse(res["ready"])
        res = self._ready_guard(
            self._deployment(), _validation_for_deployment=validation,
            _approval_exists=True,
        )
        self.assertTrue(res["ready"])

    def test_canary_active_block_is_configurable(self):
        dep = self._deployment()
        dep.service.deploy_strategy = "CANARY"
        dep.service.canary_percentage = 25
        res = self._ready_guard(dep)
        self.assertTrue(res["ready"])  # warn/allow by default
        dep.service.promotion_policy = {"block_when_canary_active": True}
        res = self._ready_guard(dep)
        self.assertFalse(res["ready"])

    def test_remote_provider_skips_docker_with_warning(self):
        from types import SimpleNamespace

        from apps.cloud.models import CloudProvider

        provider = SimpleNamespace(provider_type=CloudProvider.ProviderType.REMOTE)
        with patch(
            "apps.deployments.services.safedeploy.promotion_guard._green_container_state"
        ) as mock_state:
            res = self._ready_guard(self._deployment(), provider=provider)
        mock_state.assert_not_called()
        self.assertTrue(res["ready"])
        self.assertTrue(res["warnings"])

    def test_unknown_policy_keys_rejected_by_serializer(self):
        from apps.deployments.serializers.service import ServiceSerializer

        s = ServiceSerializer()
        with self.assertRaises(Exception):
            s.validate_promotion_policy({"nope": True})

    def test_canary_gate_only_fires_on_strategy_field_edits(self):
        """Unrelated PATCHes must not 400 while a split is active; touching
        the split fields is still gated."""
        from types import SimpleNamespace

        from rest_framework import serializers as drf_serializers

        from apps.deployments.serializers.service import ServiceSerializer

        instance = SimpleNamespace(
            id="svc-1", deploy_strategy="CANARY", canary_percentage=25,
        )
        s = ServiceSerializer(instance=instance)
        with patch(
            "apps.deployments.services.safedeploy.canary_guard.validate_canary_enable",
            return_value=(False, ["Contract op blocks shared-DB canary"]),
        ):
            self.assertEqual(s.validate({"name": "renamed"}), {"name": "renamed"})
            with self.assertRaises(drf_serializers.ValidationError):
                s.validate({"canary_percentage": 25})
            with self.assertRaises(drf_serializers.ValidationError):
                s.validate({"deploy_strategy": "CANARY"})

    def test_policy_merge_prefers_service_override(self):
        from types import SimpleNamespace

        from apps.deployments.services.safedeploy.promotion_guard import (
            get_promotion_policy,
        )

        policy = get_promotion_policy(
            SimpleNamespace(promotion_policy={"min_staging_seconds": 600})
        )
        self.assertEqual(policy["min_staging_seconds"], 600)
        self.assertTrue(policy["require_green_healthy"])
        self.assertEqual(
            get_promotion_policy(None)["min_staging_seconds"], 0
        )
