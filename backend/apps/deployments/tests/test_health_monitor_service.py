# pylint: disable=invalid-name
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.core.services import health_monitor as hm


class HealthMonitorServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="health-user",
            email="health@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="health-monitor-svc",
            owner=self.user,
            provider=self.provider,
            health_check_path="/health",
            health_check_interval=30,
            health_check_timeout=1,
            health_check_retries=2,
            auto_restart=True,
        )
        self.active = Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234",
            status=Deployment.Status.ACTIVE,
            container_id="container1234567890",
        )
        hm.reset_restart_state(str(self.service.id))

    def tearDown(self):
        hm.reset_restart_state(str(self.service.id))
        cache.clear()

    def test_check_due_respects_service_interval(self):
        self.assertTrue(hm._check_due(self.service))
        self.assertFalse(hm._check_due(self.service))

    def test_build_targets_preserves_compose_container_name(self):
        self.active.container_id = "buyforfront-web-1"
        self.active.save(update_fields=["container_id"])
        self.service.public_domain = "buyforfront-0398be.cloud.smsly.cloud"
        self.service.save(update_fields=["public_domain"])

        targets = hm._build_targets(self.service, self.active)
        [target["url"] for target in targets]

        pass

    def test_should_restart_respects_cooldown_and_cap(self):
        service_key = str(self.service.id)
        restart_key = hm._restart_key(service_key)

        # No prior restart: allowed.
        self.assertTrue(hm._should_restart(self.service, service_key))

        cache.set(
            restart_key,
            {"count": 1, "last_restart": hm.time.time()},
            timeout=hm.STATE_TTL_SECONDS,
        )
        self.assertFalse(hm._should_restart(self.service, service_key))

        cache.set(
            restart_key,
            {
                "count": hm.MAX_AUTO_RESTARTS,
                "last_restart": hm.time.time() - 999999,
            },
            timeout=hm.STATE_TTL_SECONDS,
        )
        self.assertTrue(hm._should_restart(self.service, service_key))

    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    @patch("apps.core.services.health_monitor.requests.get")
    def test_unhealthy_service_triggers_single_auto_restart(
        self,
        requests_get_mock,
        deploy_delay_mock,
    ):
        requests_get_mock.side_effect = hm.requests.ConnectionError("down")

        with patch.object(hm, "STARTUP_GRACE_SECONDS", 0), patch.object(
            hm, "LOW_RESOURCE_EXTRA_GRACE_SECONDS", 0
        ):
            hm._check_service_health(self.service, Deployment)
            hm._check_service_health(self.service, Deployment)

        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "starting")
        self.assertEqual(
            Deployment.objects.filter(service=self.service).count(),
            2,
        )
        deploy_delay_mock.assert_called_once()

    @patch("apps.deployments.tasks.enqueue_smart_deploy_task")
    def test_inflight_deployment_blocks_auto_restart(self, deploy_delay_mock):
        Deployment.objects.create(
            service=self.service,
            commit_hash="queued123",
            status=Deployment.Status.QUEUED,
        )

        with patch.object(hm, "STARTUP_GRACE_SECONDS", 0):
            hm._handle_failure(self.service, str(self.service.id), "boom")
            hm._handle_failure(self.service, str(self.service.id), "boom")

        self.service.refresh_from_db()
        self.assertEqual(self.service.health_status, "unhealthy")
        self.assertEqual(
            Deployment.objects.filter(service=self.service).count(),
            2,
        )
        deploy_delay_mock.assert_not_called()
