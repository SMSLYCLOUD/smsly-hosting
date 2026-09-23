"""WAF policy sync helpers (DB control plane -> agent local_policy.yaml).

No DB, no Docker, no broker: exercises the pure helpers — desired-mode
normalization (must mirror install.sh _harden_openappsec_desired_mode:
only exact 'prevent' enforces) and strict top-level mode extraction
(must never match override-mode or response-code-only lines).
"""
from django.test import SimpleTestCase

from apps.deployments.tasks.infra.tasks_waf import (
    extract_plain_mode,
    normalize_desired_mode,
)

SAMPLE_SHADOW = """\
# comment
webUI:
    mode: detect-learn
      override-mode: detect-learn
    mode: response-code-only
"""

SAMPLE_ENFORCE = SAMPLE_SHADOW.replace(
    "    mode: detect-learn", "    mode: prevent", 1)


class NormalizeDesiredModeTests(SimpleTestCase):
    def test_prevent_enforces(self):
        self.assertEqual(normalize_desired_mode("prevent"), "prevent")

    def test_anything_else_is_shadow(self):
        for raw in ("detect-learn", "", None, "PREVENT",
                    "enforce", "true"):
            self.assertEqual(normalize_desired_mode(raw), "detect-learn")

    def test_whitespace_padded_prevent_enforces(self):
        # Parity with install.sh (tr -d '[:space:]'): padding is stripped.
        self.assertEqual(normalize_desired_mode(" prevent "), "prevent")


class ExtractPlainModeTests(SimpleTestCase):
    def test_extracts_shadow(self):
        self.assertEqual(extract_plain_mode(SAMPLE_SHADOW), "detect-learn")

    def test_extracts_prevent(self):
        self.assertEqual(extract_plain_mode(SAMPLE_ENFORCE), "prevent")

    def test_first_match_wins(self):
        text = "mode: prevent\nmode: detect-learn\n"
        self.assertEqual(extract_plain_mode(text), "prevent")

    def test_ignores_override_and_response_modes(self):
        text = ("      override-mode: prevent\n"
                "    mode: response-code-only\n")
        self.assertIsNone(extract_plain_mode(text))

    def test_empty_or_missing(self):
        self.assertIsNone(extract_plain_mode(""))
        self.assertIsNone(extract_plain_mode(None))
        self.assertIsNone(extract_plain_mode("# no mode here\n"))
