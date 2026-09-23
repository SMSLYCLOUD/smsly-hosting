"""Unit tests for SecurityEventsView and SecurityAnalysisView.

Verifies:
- SecurityEventsView aggregates Falco JSON logs, CrowdSec bans/alerts, Fail2ban status, and Trivy CVEs.
- Fail-soft handling when subprocess calls or files are missing.
- SecurityAnalysisView calculates threat scores and returns actionable remediation steps.
"""
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.views.security import SecurityEventsView, SecurityAnalysisView


def _mock_proc(stdout="", returncode=0, stderr=""):
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class SecurityEventsTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        # The events view caches scrapes (90s): flush so each test's
        # subprocess mocks actually execute instead of hitting a stale entry.
        cache.clear()
        self.user = User.objects.create_user(username="testsec", password="password123")
        self.factory = APIRequestFactory()

    @patch("apps.core.views.security.subprocess.run")
    def test_events_aggregates_falco_and_fail2ban(self, mock_run):
        falco_json_line = json.dumps({
            "time": "2026-09-22T20:00:00Z",
            "rule": "Notice Shell spawned in container",
            "priority": "Notice",
            "output": "Shell spawned in container (user=root)",
            "output_fields": {
                "container.name": "test-app",
                "proc.name": "bash"
            }
        })

        f2b_status_output = """Status for the jail: sshd
|- Filter
|  |- Currently failed: 1
|  |- Total failed:     5
`- Actions
   |- Currently banned: 1
   |- Total banned:     2
   `- Banned IP list:   198.51.100.42
"""

        def _side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "logs" in cmd and "smsly-falco" in joined:
                return _mock_proc(stdout=f"{falco_json_line}\n")
            if "fail2ban-client" in cmd and "status" in cmd:
                return _mock_proc(stdout=f2b_status_output)
            return _mock_proc(stdout="", returncode=1)

        mock_run.side_effect = _side_effect

        req = self.factory.get("/api/v1/system/security-events/")
        force_authenticate(req, user=self.user)
        view = SecurityEventsView.as_view()
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data

        self.assertIn("summary", data)
        self.assertIn("falco_events", data)
        self.assertIn("fail2ban_jails", data)
        self.assertIn("recent_activities", data)

        self.assertEqual(len(data["falco_events"]), 1)
        self.assertEqual(data["falco_events"][0]["rule"], "Notice Shell spawned in container")

        self.assertIn("sshd", data["fail2ban_jails"])
        self.assertEqual(data["fail2ban_jails"]["sshd"]["currently_banned"], 1)
        self.assertIn("198.51.100.42", data["fail2ban_jails"]["sshd"]["banned_ips"])

        # Check unified activities feed
        activities = data["recent_activities"]
        self.assertTrue(any(a["source"] == "falco" for a in activities))
        self.assertTrue(any(a["source"] == "fail2ban" and "198.51.100.42" in a["target"] for a in activities))

    @patch("apps.core.views.security.subprocess.run")
    def test_events_fail_soft_when_tools_missing(self, mock_run):
        mock_run.side_effect = FileNotFoundError("tool not installed")

        req = self.factory.get("/api/v1/system/security-events/")
        force_authenticate(req, user=self.user)
        view = SecurityEventsView.as_view()
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary"]["falco_alerts_count"], 0)
        self.assertEqual(resp.data["summary"]["fail2ban_banned_count"], 0)

    @patch("apps.core.views.security.subprocess.run")
    def test_pills_match_feed_not_raw_collections(self, mock_run):
        # WAF pill counts feed activities: 3 info-only agent lines enter
        # the feed as INFO (previously the pill counted raw lines while
        # the feed excluded them, so the WAF tab read empty).
        agent_lines = "\n".join(json.dumps({
            "eventTime": "2026-09-22T20:00:%02dZ" % i,
            "eventName": "policy loaded",
            "eventSeverity": "info",
        }) for i in range(3))

        def _side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "logs" in cmd and "smsly-appsec-agent" in joined:
                return _mock_proc(stdout=agent_lines + "\n")
            return _mock_proc(stdout="", returncode=1)

        mock_run.side_effect = _side_effect

        req = self.factory.get("/api/v1/system/security-events/")
        force_authenticate(req, user=self.user)
        resp = SecurityEventsView.as_view()(req)

        self.assertEqual(resp.status_code, 200)
        summary = resp.data["summary"]
        oas_activities = [a for a in resp.data["recent_activities"]
                          if a["source"] == "openappsec"]
        self.assertEqual(len(oas_activities), 3)
        self.assertTrue(all(a["severity"] == "INFO" for a in oas_activities))
        self.assertEqual(summary["waf_events_count"], 3)
        self.assertEqual(summary["total_events"],
                         len(resp.data["recent_activities"]))

    @patch("apps.core.views.security.subprocess.run")
    def test_pills_stay_global_under_source_filter(self, mock_run):
        # Filtering narrows only the listed page: other pills must not
        # collapse to zero (previously per-source pills were computed
        # after filtering).
        falco_json_line = json.dumps({
            "time": "2026-09-22T20:00:00Z",
            "rule": "Terminal shell in container",
            "priority": "Warning",
            "output": "shell",
            "output_fields": {"container.name": "c", "proc.name": "sh"},
        })

        def _side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "logs" in cmd and "smsly-falco" in joined:
                return _mock_proc(stdout=f"{falco_json_line}\n")
            return _mock_proc(stdout="", returncode=1)

        mock_run.side_effect = _side_effect

        req = self.factory.get("/api/v1/system/security-events/",
                               {"source": "openappsec"})
        force_authenticate(req, user=self.user)
        resp = SecurityEventsView.as_view()(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["recent_activities"], [])
        # Global overview survives the filter: falco still counted.
        self.assertEqual(resp.data["summary"]["falco_alerts_count"], 1)
        self.assertEqual(resp.data["summary"]["total_events"], 1)

    @patch("apps.core.views.security.subprocess.run")
    def test_source_filter_limits_feed_and_total(self, mock_run):
        falco_json_line = json.dumps({
            "time": "2026-09-22T20:00:00Z",
            "rule": "Terminal shell in container",
            "priority": "Warning",
            "output": "shell",
            "output_fields": {"container.name": "c", "proc.name": "sh"},
        })

        def _side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "logs" in cmd and "smsly-falco" in joined:
                return _mock_proc(stdout=f"{falco_json_line}\n")
            return _mock_proc(stdout="", returncode=1)

        mock_run.side_effect = _side_effect

        req = self.factory.get("/api/v1/system/security-events/",
                               {"source": "falco"})
        force_authenticate(req, user=self.user)
        resp = SecurityEventsView.as_view()(req)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["recent_activities"])
        self.assertTrue(all(a["source"] == "falco"
                            for a in resp.data["recent_activities"]))
        self.assertEqual(resp.data["summary"]["total_events"],
                         len(resp.data["recent_activities"]))

    def test_stable_ids_are_deterministic(self):
        from apps.core.views.security import _stable_id

        self.assertEqual(_stable_id("oas", "line"),
                         _stable_id("oas", "line"))
        self.assertNotEqual(_stable_id("oas", "line"),
                            _stable_id("falco", "line"))
        self.assertNotEqual(_stable_id("oas", "line-a"),
                            _stable_id("oas", "line-b"))

    @patch("apps.core.views.security.subprocess.run")
    def test_second_identical_request_served_from_cache(self, mock_run):
        falco_json_line = json.dumps({
            "time": "2026-09-22T20:00:00Z",
            "rule": "Terminal shell in container",
            "priority": "Warning",
            "output": "shell",
            "output_fields": {"container.name": "c", "proc.name": "sh"},
        })

        def _side_effect(cmd, **kwargs):
            joined = " ".join(cmd)
            if "logs" in cmd and "smsly-falco" in joined:
                return _mock_proc(stdout=f"{falco_json_line}\n")
            return _mock_proc(stdout="", returncode=1)

        mock_run.side_effect = _side_effect
        view = SecurityEventsView.as_view()

        req1 = self.factory.get("/api/v1/system/security-events/")
        force_authenticate(req1, user=self.user)
        resp1 = view(req1)
        calls_after_first = mock_run.call_count
        self.assertGreater(calls_after_first, 0)

        req2 = self.factory.get("/api/v1/system/security-events/")
        force_authenticate(req2, user=self.user)
        resp2 = view(req2)
        self.assertEqual(mock_run.call_count, calls_after_first)
        self.assertEqual(resp2.data["summary"], resp1.data["summary"])
        self.assertEqual(resp2.data["recent_activities"],
                         resp1.data["recent_activities"])

    @patch("apps.core.views.security.subprocess.run")
    def test_security_analysis_generates_assessment(self, mock_run):
        mock_run.side_effect = FileNotFoundError("tool not installed")

        req = self.factory.post("/api/v1/system/security-analysis/")
        force_authenticate(req, user=self.user)
        view = SecurityAnalysisView.as_view()
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("threat_level", data)
        self.assertIn("risk_score", data)
        self.assertIn("executive_summary", data)
        self.assertIn("attack_vectors", data)
        self.assertIn("hardening_actions", data)
        self.assertTrue(isinstance(data["risk_score"], int))
        self.assertIn(data["threat_level"], ("LOW", "ELEVATED", "HIGH", "SEVERE"))
