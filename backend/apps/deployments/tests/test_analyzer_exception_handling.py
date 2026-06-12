# pylint: disable=invalid-name
"""Tests for LogAnalyzer.generate_diagnosis exception handling.

Verifies that:
- Plain Exception (e.g. ValueError) is caught and the fallback message
  is returned (existing behavior).
- KeyboardInterrupt and SystemExit are NOT swallowed — they must propagate
  so a user can still cancel a long-running AI call.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.intelligence.analyzer import LogAnalyzer


class AnalyzerExceptionHandlingTests(SimpleTestCase):
    def setUp(self):
        self.analyzer = LogAnalyzer()
        # Pad the log so it crosses the 200-char threshold and triggers
        # the AI call path.
        self.logs = ("x" * 300) + " no obvious pattern here"

    def test_value_error_falls_back_to_safe_message(self):
        with patch(
            "apps.intelligence.analyzer._cached_ask",
            side_effect=ValueError("boom"),
        ):
            result = self.analyzer.generate_diagnosis(self.logs)
        self.assertEqual(result, "No obvious issues detected.")

    def test_keyboard_interrupt_propagates(self):
        with patch(
            "apps.intelligence.analyzer._cached_ask",
            side_effect=KeyboardInterrupt(),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.analyzer.generate_diagnosis(self.logs)

    def test_system_exit_propagates(self):
        with patch(
            "apps.intelligence.analyzer._cached_ask",
            side_effect=SystemExit(1),
        ):
            with self.assertRaises(SystemExit):
                self.analyzer.generate_diagnosis(self.logs)
