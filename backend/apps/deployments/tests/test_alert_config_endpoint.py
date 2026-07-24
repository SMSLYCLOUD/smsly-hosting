# pylint: disable=invalid-name
"""Tests for ScalingViewSet.alert_config (PUT/GET endpoint)."""

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models.core import Service


class ServiceAlertConfigEndpointTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="password123",
        )
        self.other = User.objects.create_user(
            username="intruder", password="password123",
        )
        self.service = Service.objects.create(
            name="alert-cfg-svc",
            owner=self.owner,
        )
        self.url = f"/api/v1/scaling/{self.service.id}/alert_config/"

    def test_get_returns_empty_dict_for_unset_config(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {})

    def test_put_persists_alert_thresholds(self):
        self.client.force_authenticate(user=self.owner)
        payload = {
            "cpu_warning": 65,
            "cpu_critical": 88,
            "memory_warning": 70,
            "memory_critical": 92,
            "disk_warning": 80,
            "disk_critical": 95,
            "notify_email": True,
            "notify_webhook": False,
        }
        response = self.client.put(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.service.refresh_from_db()
        self.assertEqual(self.service.alert_config["cpu_warning"], 65)
        self.assertEqual(self.service.alert_config["cpu_critical"], 88)
        self.assertTrue(self.service.alert_config["notify_email"])

    def test_put_merges_with_existing_config(self):
        self.client.force_authenticate(user=self.owner)
        self.service.alert_config = {"cpu_warning": 50}
        self.service.save(update_fields=["alert_config"])

        response = self.client.put(
            self.url, {"memory_warning": 75}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.service.refresh_from_db()
        self.assertEqual(self.service.alert_config["cpu_warning"], 50)
        self.assertEqual(self.service.alert_config["memory_warning"], 75)

    def test_put_rejects_out_of_range_threshold(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            self.url, {"cpu_critical": 150}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.service.refresh_from_db()
        self.assertNotIn("cpu_critical", self.service.alert_config)

    def test_other_user_cannot_update(self):
        self.client.force_authenticate(user=self.other)
        response = self.client.put(
            self.url, {"cpu_warning": 10}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.service.refresh_from_db()
        self.assertEqual(self.service.alert_config, {})
