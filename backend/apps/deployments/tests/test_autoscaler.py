from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from apps.deployments.models import Service, Project
from apps.deployments.models_metrics import ServiceMetric
from apps.deployments.services.autoscaler import _evaluate_scaling
from django.contrib.auth import get_user_model

User = get_user_model()

class AutoscalerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="pwd")
        self.project = Project.objects.create(name="Test Proj", owner=self.user)
        self.service = Service.objects.create(
            name="test-service",
            owner=self.user,
            project=self.project,
            min_replicas=1,
            max_replicas=3,
            autoscale_cpu_target=80
        )

    def test_scale_up_respects_max_replicas(self):
        self.service.min_replicas = 3
        self.service.save()
        Service.objects.filter(id=self.service.id).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 3) # Should not exceed max_replicas

    def test_scale_up_increases_replicas(self):
        self.service.min_replicas = 1
        self.service.save()
        Service.objects.filter(id=self.service.id).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 2)

    def test_scale_down_respects_absolute_minimum(self):
        self.service.min_replicas = 1
        self.service.save()
        Service.objects.filter(id=self.service.id).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 1) # Should not drop below 1

    def test_scale_down_decreases_replicas(self):
        self.service.min_replicas = 2
        self.service.save()
        Service.objects.filter(id=self.service.id).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        # Scale down triggers if avg CPU over 5m is < 50% of target (80 * 0.5 = 40)
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 1)


    def test_cooldown_prevents_rapid_scaling(self):
        self.service.min_replicas = 1
        # Set updated_at to 30 seconds ago
        self.service.updated_at = timezone.now() - timedelta(seconds=30)
        self.service.save()

        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 1) # Should not scale up due to 1m cooldown

    def test_scale_down_cooldown(self):
        self.service.min_replicas = 2
        # Set updated_at to 3 minutes ago
        self.service.updated_at = timezone.now() - timedelta(minutes=3)
        self.service.save()

        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        self.assertEqual(self.service.min_replicas, 2) # Should not scale down due to 5m cooldown
