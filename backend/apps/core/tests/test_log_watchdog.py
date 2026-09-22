"""Unit tests for the log-error watchdog counter (pure function)."""
from unittest import TestCase

from apps.core.services.log_watchdog import count_log_errors


class TestCountLogErrors(TestCase):
    def test_empty(self):
        self.assertEqual(count_log_errors(''), 0)
        self.assertEqual(count_log_errors(None), 0)

    def test_tracebacks(self):
        text = 'ok line\nTraceback (most recent call last):\n  File "x"\nERROR done\n'
        self.assertEqual(count_log_errors(text), 2)

    def test_levels(self):
        text = '[ERROR] boom\n[CRITICAL] bad\n[FATAL] worse\n[INFO] fine\n[WARNING] meh\n'
        self.assertEqual(count_log_errors(text), 3)

    def test_lowercase_error_ignored(self):
        # Deliberate: plain "error" substrings are too noisy for alerting.
        self.assertEqual(count_log_errors('a minor error occurred\n'), 0)

    def test_structured_json(self):
        text = '{"levelname": "ERROR", "message": "x"}\n{"levelname": "INFO"}\n'
        self.assertEqual(count_log_errors(text), 1)
