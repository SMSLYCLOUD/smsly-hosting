from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.addons.views import AddonMaintenanceViewSet
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.core.rate_limiting import DBVacuumRateThrottle

User = get_user_model()


class Finding100VacuumThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vacuum-throttle", password="x",
        )
        self.service = Service.objects.create(
            name="vac-svc", owner=self.user,
        )
        self.addon = Addon.objects.create(
            service=self.service,
            name="pg-vac",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url="postgres://u:p@localhost:5432/db",
        )

    def test_vacuum_action_has_throttle_class_bound(self):
        action = getattr(AddonMaintenanceViewSet, "vacuum", None)
        self.assertIsNotNone(action)
        bound = (
            getattr(action, "throttle_classes", None)
            or getattr(action, "kwargs", {}).get("throttle_classes")
            or []
        )
        self.assertIn(DBVacuumRateThrottle, bound)

    def test_second_call_within_hour_is_rejected(self):
        factory = APIRequestFactory()
        action = AddonMaintenanceViewSet.vacuum
        initkwargs = getattr(action, "kwargs", {}) or {}
        view = AddonMaintenanceViewSet.as_view(
            {"post": "vacuum"}, **initkwargs,
        )
        pk = str(self.addon.id)

        with patch(
            "apps.addons.views.addons.AddonMaintenanceService"
        ) as mock_service_class:
            mock_service_class.return_value.vacuum_analyze.return_value = None

            req1 = factory.post(f"/maintenance/{pk}/vacuum/")
            force_authenticate(req1, user=self.user)
            resp1 = view(req1, pk=pk)
            self.assertEqual(
                resp1.status_code,
                status.HTTP_200_OK,
                f"First call should succeed; got {resp1.status_code}",
            )

            req2 = factory.post(f"/maintenance/{pk}/vacuum/")
            force_authenticate(req2, user=self.user)
            resp2 = view(req2, pk=pk)
            self.assertEqual(
                resp2.status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Second call within the hour should be throttled; "
                f"got {resp2.status_code}",
            )
