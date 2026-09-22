# pylint: disable=invalid-name
"""Tests for the green reaper (failed-rollout container stops)."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.deploy.green_reaper import reap_failed_greens_task

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "green-reaper-tests",
    }
}


def _container(state='running', health='unhealthy', started_minutes_ago=50):
    started = (timezone.now() - timedelta(minutes=started_minutes_ago)).isoformat()
    container = MagicMock()
    container.attrs = {'State': {'Status': state, 'Health': {'Status': health}, 'StartedAt': started}}
    return container


import django.test


@override_settings(CACHES=TEST_CACHES)
class ReapFailedGreensTests(django.test.TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='reaper', email='reaper@test.com', password='testpass123')
        self.provider = CloudProvider.objects.create(
            name='test-provider', provider_type='LOCAL', is_active=True)
        self.service = Service.objects.create(
            name='reap-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider,
        )

    def _deploy(self, st, green=None, container=None):
        return Deployment.objects.create(
            service=self.service,
            status=st,
            commit_hash='abc123',
            green_container_id=green or '',
            container_id=container or '',
        )

    def test_stops_running_green_of_failed_deploy(self):
        self._deploy(Deployment.Status.FAILED, green='green-1')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            reap_failed_greens_task.run()
        runtime_cls.return_value.stop_container.assert_called_once_with('green-1')

    def test_skips_ids_referenced_by_active_blue(self):
        self._deploy(Deployment.Status.ACTIVE, container='blue-1')
        self._deploy(Deployment.Status.FAILED, green='blue-1')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            reap_failed_greens_task.run()
        runtime_cls.return_value.stop_container.assert_not_called()

    def test_reaps_wedged_staged_green(self):
        self._deploy(Deployment.Status.STAGED, green='green-2')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            runtime_cls.return_value.get_container.return_value = _container(
                state='running', health='starting', started_minutes_ago=50)
            reap_failed_greens_task.run()
        runtime_cls.return_value.stop_container.assert_called_once_with('green-2')

    def test_leaves_fresh_staged_green_alone(self):
        self._deploy(Deployment.Status.STAGED, green='green-3')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            runtime_cls.return_value.get_container.return_value = _container(
                state='running', health='starting', started_minutes_ago=5)
            reap_failed_greens_task.run()
        runtime_cls.return_value.stop_container.assert_not_called()

    def test_leaves_healthy_staged_green_alone(self):
        self._deploy(Deployment.Status.STAGED, green='green-4')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            runtime_cls.return_value.get_container.return_value = _container(
                state='running', health='healthy', started_minutes_ago=90)
            reap_failed_greens_task.run()
        runtime_cls.return_value.stop_container.assert_not_called()
