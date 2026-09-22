# pylint: disable=invalid-name
"""Tests for real stop/start service lifecycle (container actually stops)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.core.services import health_monitor

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "service-stop-start-tests",
    }
}


def _make_service(user, provider, name='stop-svc', status='ACTIVE'):
    service = Service.objects.create(
        name=name,
        repository_url='https://github.com/test/app',
        owner=user,
        provider=provider,
        status=status,
    )
    Deployment.objects.create(
        service=service,
        status=Deployment.Status.ACTIVE,
        commit_hash='abc123',
        container_id='container-123',
    )
    return service


@override_settings(CACHES=TEST_CACHES)
class ServiceStopStartTests(APITestCase):
    """Stop halts the container + flips status; start reverses it."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='stopper', email='stopper@test.com', password='testpass123')
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name='test-provider', provider_type='LOCAL', is_active=True)

    def test_stop_stops_container_and_marks_stopped(self):
        service = _make_service(self.user, self.provider)
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            response = self.client.post(f'/api/v1/services/{service.id}/stop/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        runtime_cls.return_value.stop_container.assert_called_once_with('container-123')
        service.refresh_from_db()
        self.assertEqual(service.status, 'STOPPED')
        self.assertEqual(service.health_status, 'unknown')
        # ACTIVE row is kept as the resume point, not cancelled.
        self.assertTrue(service.deployments.filter(status=Deployment.Status.ACTIVE).exists())

    def test_stop_is_idempotent(self):
        service = _make_service(self.user, self.provider, status='STOPPED')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            response = self.client.post(f'/api/v1/services/{service.id}/stop/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        runtime_cls.return_value.stop_container.assert_not_called()

    def test_start_requires_stopped(self):
        service = _make_service(self.user, self.provider)
        response = self.client.post(f'/api/v1/services/{service.id}/start/')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_start_starts_container_and_marks_active(self):
        service = _make_service(self.user, self.provider, status='STOPPED')
        with patch(
            'apps.deployments.services.container_runtime.ContainerRuntime'
        ) as runtime_cls:
            response = self.client.post(f'/api/v1/services/{service.id}/start/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        runtime_cls.return_value.start_container.assert_called_once_with('container-123')
        service.refresh_from_db()
        self.assertEqual(service.status, 'ACTIVE')
        self.assertEqual(service.health_status, 'starting')

    def test_start_without_deployment_row_fails(self):
        service = Service.objects.create(
            name='bare-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider,
            status='STOPPED',
        )
        response = self.client.post(f'/api/v1/services/{service.id}/start/')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_health_monitor_skips_stopped(self):
        service = _make_service(self.user, self.provider, name='skip-svc', status='STOPPED')
        with patch(
            'apps.core.services.health_monitor.requests.get'
        ) as get:
            health_monitor._check_service_health(service, Deployment)
        get.assert_not_called()
        service.refresh_from_db()
        self.assertEqual(service.status, 'STOPPED')

    def test_new_deploy_revives_stopped_service(self):
        svc = _make_service(self.user, self.provider, name='revive-svc', status='STOPPED')
        Deployment.objects.create(
            service=svc, status=Deployment.Status.BUILDING, commit_hash='def456')
        svc.refresh_from_db()
        self.assertEqual(svc.status, 'ACTIVE')

    def test_active_row_save_keeps_stopped(self):
        svc = _make_service(self.user, self.provider, name='keep-svc', status='STOPPED')
        row = svc.deployments.filter(status=Deployment.Status.ACTIVE).first()
        row.save()
        svc.refresh_from_db()
        self.assertEqual(svc.status, 'STOPPED')

    def test_container_runtime_stop_start_helpers(self):
        from apps.deployments.services.container_runtime import ContainerRuntime
        with patch('docker.from_env') as from_env:
            container = MagicMock()
            container.attrs = {'State': {'Status': 'running'}}
            from_env.return_value.containers.get.return_value = container
            ContainerRuntime().stop_container('c1')
            container.stop.assert_called_once()
            container.status = None
            container.attrs = {'State': {'Status': 'exited'}}
            ContainerRuntime().start_container('c1')
            container.start.assert_called_once()
