# pylint: disable=invalid-name
"""Tests for bulk power operations and the auto-off timer."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.services import power as power_mod

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "power-tests",
    }
}


def _svc(user, provider, name, status='ACTIVE', container='c-1'):
    service = Service.objects.create(
        name=name, repository_url='https://github.com/test/app',
        owner=user, provider=provider, status=status)
    Deployment.objects.create(
        service=service, status=Deployment.Status.ACTIVE,
        commit_hash='abc123', container_id=container)
    return service


@override_settings(CACHES=TEST_CACHES)
class PowerHelpersTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='power', email='power@test.com', password='testpass123')
        self.provider = CloudProvider.objects.create(
            name='test-provider', provider_type='LOCAL', is_active=True)

    def test_power_all_stops_active(self):
        _svc(self.user, self.provider, 'a-svc')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ):
            summary = power_mod.power_all('stop', actor='test')
        self.assertEqual(summary['done'], ['a-svc'])
        self.assertEqual(
            Service.objects.get(name='a-svc').status, 'STOPPED')

    def test_power_all_collects_errors(self):
        # A docker failure still flips state (missing/dead == stopped) and
        # records the method — it must not land in `failed`.
        _svc(self.user, self.provider, 'b-svc')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            runtime_cls.return_value.stop_container.side_effect = RuntimeError('no docker')
            summary = power_mod.power_all('stop', actor='test')
        self.assertEqual(summary['done'], ['b-svc'])
        self.assertEqual(summary['failed'], {})
        from apps.deployments.models import Service
        self.assertEqual(Service.objects.get(name='b-svc').status, 'STOPPED')

    def test_power_all_records_unexpected_errors(self):
        _svc(self.user, self.provider, 'd-svc')
        with patch(
            'apps.deployments.services.power.stop_service',
            side_effect=RuntimeError('boom'),
        ):
            summary = power_mod.power_all('stop', actor='test')
        self.assertEqual(summary['done'], [])
        self.assertIn('d-svc', summary['failed'])

    def test_restart_service_marks_active_from_stopped(self):
        svc = _svc(self.user, self.provider, 'c-svc', status='STOPPED')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ):
            result = power_mod.restart_service(svc, actor='test')
        self.assertTrue(result['ok'])
        svc.refresh_from_db()
        self.assertEqual(svc.status, 'ACTIVE')


@override_settings(CACHES=TEST_CACHES)
class PowerEndpointsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='pleb', email='pleb@test.com', password='testpass123')
        self.admin = User.objects.create_superuser(
            username='root', email='root@test.com', password='testpass123')
        self.provider = CloudProvider.objects.create(
            name='test-provider', provider_type='LOCAL', is_active=True)

    def test_bulk_requires_admin(self):
        self.client.force_authenticate(user=self.user)
        for path in ['bulk-stop', 'bulk-start', 'bulk-restart']:
            response = self.client.post(f'/api/v1/services/{path}/')
            self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_bulk_queues_task(self):
        self.client.force_authenticate(user=self.admin)
        with patch(
            'apps.deployments.tasks.deploy.power_tasks.bulk_power_task'
        ) as task:
            task.delay.return_value = MagicMock(id='task-1')
            response = self.client.post('/api/v1/services/bulk-stop/')
        self.assertEqual(response.status_code, http_status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data['task_id'], 'task-1')

    def test_auto_off_schedule_and_cancel(self):
        self.client.force_authenticate(user=self.admin)
        from types import SimpleNamespace
        with patch(
            'apps.deployments.tasks.deploy.power_tasks.bulk_power_task'
        ) as task:
            task.apply_async.return_value = SimpleNamespace(id='task-1')
            response = self.client.post('/api/v1/services/auto-off/', {'in_minutes': 30})
        self.assertEqual(response.status_code, http_status.HTTP_202_ACCEPTED)
        self.assertIn('fires_at', response.data['scheduled'])
        response = self.client.get('/api/v1/services/auto-off/')
        self.assertIsNotNone(response.data['pending'])
        response = self.client.delete('/api/v1/services/auto-off/')
        self.assertTrue(response.data['cancelled'])
        response = self.client.get('/api/v1/services/auto-off/')
        self.assertIsNone(response.data['pending'])

    def test_auto_off_rejects_bad_minutes(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/v1/services/auto-off/', {'in_minutes': 0})
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        response = self.client.post('/api/v1/services/auto-off/', {'in_minutes': 9999})
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
