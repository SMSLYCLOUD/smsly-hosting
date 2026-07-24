"""Unit tests for Traffic Map geolocation API and log upserts."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models.traffic import ServiceTrafficLog
from apps.core.tasks.traffic import _upsert_traffic_row

User = get_user_model()


class TrafficMapTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pwd")
        self.other_user = User.objects.create_user(username="other", password="pwd")

        self.project = Project.objects.create(name="Traffic Proj", owner=self.user)
        self.service = Service.objects.create(
            name="traffic-svc",
            owner=self.user,
            project=self.project,
            public_domain="app.smsly.cloud",
        )

        # Create sample resolved traffic logs
        ServiceTrafficLog.objects.create(
            service=self.service,
            ip_address="8.8.8.8",
            domain="app.smsly.cloud",
            country_code="US",
            country_name="United States",
            city="Mountain View",
            latitude=37.386,
            longitude=-122.0838,
            request_count=80,
            geo_resolved=True,
        )
        ServiceTrafficLog.objects.create(
            service=self.service,
            ip_address="1.1.1.1",
            domain="app.smsly.cloud",
            country_code="AU",
            country_name="Australia",
            city="Sydney",
            latitude=-33.8688,
            longitude=151.2093,
            request_count=20,
            geo_resolved=True,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_traffic_geo_api_returns_aggregated_stats_and_coordinates(self):
        response = self.client.get(f"/api/v1/services/{self.service.id}/traffic-geo/")
        self.assertEqual(response.status_code, 200)

        data = response.data
        self.assertEqual(data["total_requests"], 100)
        self.assertEqual(data["unique_ips"], 2)
        self.assertEqual(data["unique_countries"], 2)

        us_entry = next(item for item in data["countries"] if item["code"] == "US")
        self.assertEqual(us_entry["count"], 80)
        self.assertEqual(us_entry["percentage"], 80.0)
        self.assertAlmostEqual(us_entry["latitude"], 37.386, places=3)
        self.assertAlmostEqual(us_entry["longitude"], -122.0838, places=3)

        mv_city = next(item for item in data["top_cities"] if item["city"] == "Mountain View")
        self.assertEqual(mv_city["count"], 80)
        self.assertAlmostEqual(mv_city["latitude"], 37.386, places=3)

    def test_traffic_geo_api_rejects_unauthorized_user(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(f"/api/v1/services/{self.service.id}/traffic-geo/")
        self.assertEqual(response.status_code, 403)

    def test_upsert_traffic_row_creates_and_increments_count(self):
        # New IP
        _upsert_traffic_row("9.9.9.9", "app.smsly.cloud")
        log_entry = ServiceTrafficLog.objects.get(service=self.service, ip_address="9.9.9.9")
        self.assertEqual(log_entry.request_count, 1)

        # Second request from same IP
        _upsert_traffic_row("9.9.9.9", "app.smsly.cloud")
        log_entry.refresh_from_db()
        self.assertEqual(log_entry.request_count, 2)
