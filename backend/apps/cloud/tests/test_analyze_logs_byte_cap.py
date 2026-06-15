from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


User = get_user_model()


class AnalyzeLogsByteCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="analyze-logs-cap",
            password="pw",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.intelligence.analyzer.LogAnalyzer")
    def test_oversized_logs_rejected(self, mock_analyzer_cls):
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        resp = self.client.post(
            "/api/v1/cloud/intelligence/analyze_logs/",
            {"logs": "x" * 70000},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_analyzer.assert_not_called()

    @patch("apps.intelligence.analyzer.LogAnalyzer")
    def test_list_of_logs_within_cap_accepted(self, mock_analyzer_cls):
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_logs.return_value = {"summary": "ok"}
        mock_analyzer_cls.return_value = mock_analyzer
        resp = self.client.post(
            "/api/v1/cloud/intelligence/analyze_logs/",
            {"logs": ["line one", "line two", "line three"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        mock_analyzer.analyze_logs.assert_called_once()

    @patch("apps.intelligence.analyzer.LogAnalyzer")
    def test_large_list_rejected(self, mock_analyzer_cls):
        mock_analyzer = MagicMock()
        mock_analyzer_cls.return_value = mock_analyzer
        big = "x" * 70000
        resp = self.client.post(
            "/api/v1/cloud/intelligence/analyze_logs/",
            {"logs": [big]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_analyzer.assert_not_called()
