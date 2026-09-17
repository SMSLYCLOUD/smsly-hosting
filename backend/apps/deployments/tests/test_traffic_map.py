"""Unit tests for Traffic Map geolocation API and log upserts."""
from unittest.mock import MagicMock

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models.traffic import ServiceTrafficLog
from apps.core.tasks.traffic import (
    _resolve_via_ipapi,
    _resolve_via_ipwho,
    _upsert_traffic_row,
)

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


def _resp(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


class GeoResolverTests(SimpleTestCase):
    """ipwho.is (HTTPS primary) + ip-api.com (HTTP fallback) mapping."""

    def test_ipwho_success_maps_fields(self):
        session_get = MagicMock(return_value=_resp({
            "success": True, "country_code": "NG", "country": "Nigeria",
            "city": "Lagos", "latitude": 6.5244, "longitude": 3.3792,
        }))
        outcome, geo = _resolve_via_ipwho(session_get, "102.91.77.12")
        self.assertEqual(outcome, "ok")
        self.assertEqual(geo["country_code"], "NG")
        self.assertEqual(geo["city"], "Lagos")
        self.assertAlmostEqual(geo["latitude"], 6.5244)
        session_get.assert_called_once()
        self.assertIn("https://", session_get.call_args[0][0])

    def test_ipwho_rate_limit_backs_off(self):
        session_get = MagicMock(return_value=_resp(
            {"success": False, "message": "Rate limit exceeded, try again later"}))
        outcome, geo = _resolve_via_ipwho(session_get, "1.2.3.4")
        self.assertEqual(outcome, "limited")
        self.assertIsNone(geo)

    def test_ipwho_invalid_ip_is_dead(self):
        session_get = MagicMock(return_value=_resp(
            {"success": False, "message": "Invalid IP address"}))
        outcome, _ = _resolve_via_ipwho(session_get, "999.1.1.1")
        self.assertEqual(outcome, "dead")

    def test_ipwho_transport_error(self):
        session_get = MagicMock(side_effect=requests.ConnectionError("down"))
        outcome, geo = _resolve_via_ipwho(session_get, "1.2.3.4")
        self.assertEqual(outcome, "error")
        self.assertIsNone(geo)

    def test_ipapi_success_maps_fields(self):
        session_get = MagicMock(return_value=_resp({
            "status": "success", "countryCode": "US", "country": "United States",
            "city": "Ashburn", "lat": 39.03, "lon": -77.5,
        }))
        outcome, geo = _resolve_via_ipapi(session_get, "54.1.2.3")
        self.assertEqual(outcome, "ok")
        self.assertEqual(geo["country_code"], "US")
        self.assertAlmostEqual(geo["longitude"], -77.5)

    def test_ipapi_fail_is_dead(self):
        session_get = MagicMock(return_value=_resp(
            {"status": "fail", "message": "private range"}))
        outcome, _ = _resolve_via_ipapi(session_get, "10.0.0.1")
        self.assertEqual(outcome, "dead")
