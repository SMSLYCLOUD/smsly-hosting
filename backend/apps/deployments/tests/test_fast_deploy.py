"""Tests for the fast-deploy path.

Fast deploy = no AI analysis, no review gates, straight to live (ACTIVE).
Resolution precedence: explicit param / deployment row flag >
per-service ``fast_deploy_enabled`` override > platform-wide
``fast_deploy_default``.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, PlatformConfig, Service
from apps.deployments.services.pipeline.manager import PipelineManager
from apps.deployments.tasks.tasks_utils import resolve_fast_deploy

User = get_user_model()


def _make_service(user, name="fast-svc", **kwargs):
    params = {
        "name": name,
        "owner": user,
        "deploy_type": "GIT",
        "repository_url": "",
        "branch": "main",
    }
    params.update(kwargs)
    return Service.objects.create(**params)


class ResolveFastDeployTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fast-resolve", password="p")
        self.service = _make_service(self.user)
        self.config = PlatformConfig.load()

    def test_defaults_to_full_review_path(self):
        self.config.fast_deploy_default = False
        self.config.save()
        self.service.fast_deploy_enabled = None
        self.service.save()
        self.assertFalse(resolve_fast_deploy(self.service, self.config))

    def test_platform_default_enables(self):
        self.config.fast_deploy_default = True
        self.config.save()
        self.service.fast_deploy_enabled = None
        self.service.save()
        self.assertTrue(resolve_fast_deploy(self.service, self.config))

    def test_service_true_beats_platform_false(self):
        self.config.fast_deploy_default = False
        self.config.save()
        self.service.fast_deploy_enabled = True
        self.service.save()
        self.assertTrue(resolve_fast_deploy(self.service, self.config))

    def test_service_false_beats_platform_true(self):
        self.config.fast_deploy_default = True
        self.config.save()
        self.service.fast_deploy_enabled = False
        self.service.save()
        self.assertFalse(resolve_fast_deploy(self.service, self.config))

    def test_never_raises(self):
        self.assertFalse(resolve_fast_deploy(None, None))


class PipelineSkipAnalysisTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fast-pipe", password="p")
        self.service = _make_service(self.user)
        self.deployment = Deployment.objects.create(
            service=self.service, commit_hash="abc1234",
            status=Deployment.Status.QUEUED,
        )

    def _run(self, **kwargs):
        mgr = PipelineManager(self.deployment, **kwargs)
        patches = [
            patch.object(PipelineManager, "_setup"),
            patch.object(PipelineManager, "_capture_pre_deploy_snapshot"),
            patch.object(PipelineManager, "_clone_repo"),
            patch.object(PipelineManager, "_run_ai_analysis"),
            patch.object(PipelineManager, "_inject_env_vars"),
            patch.object(PipelineManager, "_auto_provision_addons"),
            patch.object(PipelineManager, "_push_image"),
            patch.object(PipelineManager, "_sign_image"),
            patch.object(PipelineManager, "_verify_signature"),
            patch.object(PipelineManager, "_cleanup"),
            patch(
                "apps.deployments.services.pipeline.manager"
                ".log_exhaustive_network_and_routing_diagnostics"
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        build = patch.object(PipelineManager, "_build_image").start()
        self.addCleanup(build.stop)
        build.side_effect = lambda: setattr(mgr, "image_name", "registry:5000/x")
        analysis = PipelineManager._run_ai_analysis
        mgr.run()
        return analysis

    def test_run_executes_analysis_by_default(self):
        analysis = self._run()
        analysis.assert_called_once()

    def test_skip_analysis_skips_ai_but_builds(self):
        mgr = PipelineManager(self.deployment, skip_analysis=True)
        with patch.object(PipelineManager, "_setup"), \
                patch.object(PipelineManager, "_capture_pre_deploy_snapshot"), \
                patch.object(PipelineManager, "_clone_repo") as clone, \
                patch.object(PipelineManager, "_run_ai_analysis") as analysis, \
                patch.object(PipelineManager, "_inject_env_vars") as inject, \
                patch.object(PipelineManager, "_auto_provision_addons") as addons, \
                patch.object(PipelineManager, "_push_image"), \
                patch.object(PipelineManager, "_sign_image"), \
                patch.object(PipelineManager, "_verify_signature"), \
                patch.object(PipelineManager, "_cleanup"), \
                patch("apps.deployments.services.pipeline.manager"
                      ".log_exhaustive_network_and_routing_diagnostics"), \
                patch.object(PipelineManager, "_build_image") as build:
            build.side_effect = lambda: setattr(mgr, "image_name", "registry:5000/x")
            self.assertEqual(mgr.run(), "registry:5000/x")
        analysis.assert_not_called()
        clone.assert_called_once()
        inject.assert_called_once()
        addons.assert_called_once()


@override_settings(SENATE_ENABLED=False)
class SmartDeployFastPathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fast-task", password="p")
        self.service = _make_service(self.user)
        self.provider = CloudProvider.objects.create(
            name="fast-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def _run_task(self, deployment, **task_kwargs):
        from apps.deployments.tasks.deployment import tasks_deploy as td
        with patch.object(td, "PipelineManager") as mock_pm, \
                patch.object(td, "_deploy_container") as mock_deploy, \
                patch.object(td, "_resolve_provider_for_service") as mock_resolve, \
                patch.object(td, "fleet_build_lock") as mock_lock, \
                patch.object(td, "_deployment_effective_server") as mock_eff, \
                patch.object(td, "_is_local_deployment_server", return_value=True), \
                patch("apps.deployments.tasks.cicd.tasks_commit_status"
                      ".update_commit_status"):
            mock_resolve.return_value = self.provider
            mock_eff.return_value = object()
            instance = mock_pm.return_value
            instance.run.return_value = "registry:5000/fast"
            td.smart_deploy_task.apply(
                args=[str(deployment.id), ""],
                kwargs=task_kwargs,
            )
        return mock_pm, mock_deploy

    def test_fast_goes_live_without_analysis_or_review(self):
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="fast123",
            status=Deployment.Status.QUEUED, is_fast_deploy=True,
        )
        mock_pm, mock_deploy = self._run_task(deployment, fast_deploy=True)
        mock_pm.assert_called_once()
        self.assertTrue(mock_pm.call_args.kwargs.get("skip_analysis"))
        self.assertFalse(mock_pm.call_args.kwargs.get("staged_only"))
        instance = mock_pm.return_value
        instance.run.assert_called_once()
        instance.run_analysis_only.assert_not_called()
        mock_deploy.assert_called_once()
        self.assertFalse(mock_deploy.call_args.kwargs.get("staged_only"))
        deployment.refresh_from_db()
        self.assertNotEqual(deployment.status, Deployment.Status.REVIEW)

    def test_skip_review_without_fast_still_stages(self):
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="stage123",
            status=Deployment.Status.QUEUED,
        )
        mock_pm, mock_deploy = self._run_task(deployment, skip_review=True)
        self.assertFalse(mock_pm.call_args.kwargs.get("skip_analysis"))
        self.assertTrue(mock_pm.call_args.kwargs.get("staged_only"))
        self.assertTrue(mock_deploy.call_args.kwargs.get("staged_only"))

    def test_fast_skips_safedeploy_gate(self):
        self.service.safedeploy_enabled = True
        self.service.save()
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="safe123",
            status=Deployment.Status.QUEUED, is_fast_deploy=True,
        )
        from apps.deployments.tasks.deployment import tasks_deploy as td
        with patch.object(td, "PipelineManager") as mock_pm, \
                patch.object(td, "_deploy_container"), \
                patch.object(td, "_resolve_provider_for_service",
                             return_value=self.provider), \
                patch.object(td, "fleet_build_lock"), \
                patch.object(td, "_deployment_effective_server"), \
                patch.object(td, "_is_local_deployment_server", return_value=True), \
                patch("apps.deployments.tasks.cicd.tasks_commit_status"
                      ".update_commit_status"), \
                patch("apps.deployments.services.safedeploy.deployment_pipeline"
                      ".ProductionDeploymentPipeline") as mock_pipe:
            mock_pm.return_value.run.return_value = "registry:5000/fast"
            td.smart_deploy_task.apply(
                args=[str(deployment.id), ""],
                kwargs={"fast_deploy": True},
            )
        mock_pipe.assert_not_called()
        mock_pm.return_value.run.assert_called_once()

    def test_service_config_enables_fast_without_param(self):
        self.service.fast_deploy_enabled = True
        self.service.save()
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="cfg123",
            status=Deployment.Status.QUEUED,
        )
        mock_pm, mock_deploy = self._run_task(deployment)
        self.assertTrue(mock_pm.call_args.kwargs.get("skip_analysis"))
        self.assertFalse(mock_deploy.call_args.kwargs.get("staged_only"))

    def test_service_opt_out_beats_platform_default(self):
        config = PlatformConfig.load()
        config.fast_deploy_default = True
        config.save()
        self.service.fast_deploy_enabled = False
        self.service.save()
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="opt123",
            status=Deployment.Status.QUEUED,
        )
        mock_pm, mock_deploy = self._run_task(deployment)
        instance = mock_pm.return_value
        instance.run_analysis_only.assert_called_once()
        instance.run.assert_not_called()
        config.fast_deploy_default = False
        config.save()


class EnqueueFastDeployTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fast-queue", password="p")
        self.service = _make_service(self.user)
        self.provider = CloudProvider.objects.create(
            name="fast-q-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def test_enqueue_forwards_fast_flag(self):
        with patch("apps.deployments.tasks.deployment.tasks_deploy.smart_deploy_task") as mock_task:
            from apps.deployments.tasks.deploy.queue import enqueue_smart_deploy_task
            enqueue_smart_deploy_task("dep-id", "prov-id", fast_deploy=True)
        mock_task.delay.assert_called_once_with(
            deployment_id="dep-id", provider_id="prov-id",
            skip_review=False, fast_deploy=True,
        )

    def test_recovery_preserves_fast_flag(self):
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="rec123",
            status=Deployment.Status.QUEUED, is_fast_deploy=True,
        )
        from apps.deployments.tasks.deploy import queue as q
        with patch.object(q, "AsyncResult") as mock_result, \
                patch.object(q, "_resolve_provider_for_service",
                             return_value=self.provider), \
                patch.object(q, "enqueue_smart_deploy_task") as mock_enqueue:
            mock_result.return_value.state = "PENDING"
            from apps.deployments.tasks.deploy.queue import (
                recover_stalled_queued_deployments,
            )
            recover_stalled_queued_deployments()
        mock_enqueue.assert_called_once()
        self.assertTrue(mock_enqueue.call_args.kwargs.get("fast_deploy"))


class FastDeployApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="fast-api", password="p")
        self.client.force_authenticate(self.user)
        self.service = _make_service(self.user, name="fast-api-svc")
        self.provider = CloudProvider.objects.create(
            name="fast-api-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def test_trigger_rejects_fast_deploy_for_users(self):
        resp = self.client.post("/api/v1/deployments/trigger/", {
            "service_id": str(self.service.id),
            "provider_id": str(self.provider.id),
            "fast_deploy": True,
        }, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_fast_flag_visible_on_deployment(self):
        deployment = Deployment.objects.create(
            service=self.service, commit_hash="vis123",
            status=Deployment.Status.QUEUED, is_fast_deploy=True,
        )
        self.assertIn("[FAST]", str(deployment))
        from apps.deployments.serializers import DeploymentSerializer
        data = DeploymentSerializer(deployment).data
        self.assertTrue(data["is_fast_deploy"])
