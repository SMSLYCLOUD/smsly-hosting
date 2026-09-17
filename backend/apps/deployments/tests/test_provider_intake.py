"""Provider intake: every dispatch path must land on the right provider.

- The views re-export must agree with the canonical resolver in all
  scenarios (single implementation, no silent divergence).
- smart/resume_deploy_task with an unknown provider id must fail the
  deployment loudly (not retry limbo, not a wrong target).
- An inactive provider id must fail loudly, never deploy to a dead target.
- A missing provider id must resolve (prefer local) and proceed.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.test.utils import override_settings

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service


def _svc(user, provider, name="intake-svc", **kwargs):
    return Service.objects.create(
        name=name, owner=user, provider=provider, **kwargs
    )


class ResolverParityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="parity-user", password="x")
        self.active = CloudProvider.objects.create(
            name="parity-active",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.dead = CloudProvider.objects.create(
            name="parity-dead",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=False,
        )

    def _both(self, service, **kwargs):
        from apps.deployments.tasks.deploy.provider import (
            _resolve_provider_for_service as canonical,
        )
        from apps.deployments.views._helpers import (
            _resolve_provider_for_service as reexport,
        )
        expected = canonical(service, **kwargs)
        actual = reexport(service, **kwargs)
        self.assertEqual(
            actual.id if actual else None,
            expected.id if expected else None,
        )
        return actual

    def test_assigned_active_returns_it(self):
        svc = _svc(self.user, self.active)
        self.assertEqual(self._both(svc).id, self.active.id)

    def test_assigned_inactive_returns_none(self):
        svc = _svc(self.user, self.dead)
        self.assertIsNone(self._both(svc))

    def test_unassigned_prefers_local(self):
        svc = _svc(self.user, None)
        self.assertEqual(self._both(svc, prefer_local=True).id, self.active.id)

    def test_unassigned_no_local_returns_none(self):
        self.active.is_active = False
        self.active.save(update_fields=["is_active"])
        self.dead.is_active = False
        self.dead.save(update_fields=["is_active"])
        svc = _svc(self.user, None)
        self.assertIsNone(self._both(svc, prefer_local=True))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class SmartDeployIntakeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="intake-user", password="x")
        self.provider = CloudProvider.objects.create(
            name="intake-local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="intake-svc",
            owner=self.user,
            provider=self.provider,
            deploy_type="DOCKER",
            docker_image="nginx:latest",
        )

    def _deploy(self):
        return Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="abc1234",
        )

    @patch("apps.deployments.tasks.deployment.tasks_deploy._handle_failure")
    def test_unknown_provider_id_fails_loudly(self, mock_handle):
        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task

        dep = self._deploy()
        smart_deploy_task(str(dep.id), "00000000-0000-0000-0000-000000000000")

        mock_handle.assert_called_once()
        _task_self, _dep_arg, reason, _kind = mock_handle.call_args[0]
        self.assertIn("not found", reason)

    @patch("apps.deployments.tasks.deployment.tasks_deploy._handle_failure")
    def test_inactive_provider_id_refuses_dead_target(self, mock_handle):
        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task

        self.provider.is_active = False
        self.provider.save(update_fields=["is_active"])
        dep = self._deploy()
        smart_deploy_task(str(dep.id), str(self.provider.id))

        mock_handle.assert_called_once()
        _task_self, _dep_arg, reason, _kind = mock_handle.call_args[0]
        self.assertIn("not active", reason)

    @patch("apps.deployments.tasks.deployment.tasks_deploy._deploy_container")
    @override_settings(SENATE_ENABLED=False)
    def test_missing_provider_id_resolves_and_proceeds(self, mock_deploy):
        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task

        self.service.provider = None
        self.service.save(update_fields=["provider"])
        dep = self._deploy()
        smart_deploy_task(str(dep.id), None, skip_review=True, fast_deploy=True)

        mock_deploy.assert_called_once()
