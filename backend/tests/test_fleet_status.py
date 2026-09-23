"""Worker-fleet status helpers (performance control plane).

No DB, no Docker, no broker: exercises the pure helpers on
SystemConfigView — autoscale arg parsing and burst-queue coverage.
"""
from django.test import SimpleTestCase

from apps.core.views.system import SystemConfigView


class ParseAutoscaleArgTests(SimpleTestCase):
    def test_equals_form(self):
        self.assertEqual(
            SystemConfigView._parse_autoscale_arg(
                ["celery", "-A", "config", "worker", "--autoscale=3,0"]),
            "3,0",
        )

    def test_split_form(self):
        self.assertEqual(
            SystemConfigView._parse_autoscale_arg(
                ["celery", "worker", "--autoscale", "2,1"]),
            "2,1",
        )

    def test_missing_returns_none(self):
        self.assertIsNone(
            SystemConfigView._parse_autoscale_arg(["celery", "worker", "-Q", "fast"]))
        self.assertIsNone(SystemConfigView._parse_autoscale_arg([]))
        self.assertIsNone(SystemConfigView._parse_autoscale_arg(None))


class CoverageWarningsTests(SimpleTestCase):
    def test_covered_queues_no_warnings(self):
        self.assertEqual(
            SystemConfigView._coverage_warnings("celery,fast,deploy", 0, 0), [])

    def test_uncovered_fast_warns(self):
        warnings = SystemConfigView._coverage_warnings("celery,deploy", 0, 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("fast", warnings[0])

    def test_uncovered_deploy_warns(self):
        warnings = SystemConfigView._coverage_warnings("celery,fast", 0, 0)
        self.assertEqual(len(warnings), 1)
        self.assertIn("deploy", warnings[0])

    def test_both_uncovered_warns_twice(self):
        warnings = SystemConfigView._coverage_warnings("celery", 0, 0)
        self.assertEqual(len(warnings), 2)

    def test_nonzero_min_needs_no_coverage(self):
        # Burst worker always keeps a child: queue drains without main.
        self.assertEqual(
            SystemConfigView._coverage_warnings("celery", 1, 1), [])
