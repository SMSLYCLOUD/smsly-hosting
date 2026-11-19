"""WAF policy sync helpers (DB control plane -> agent local_policy.yaml).

No DB, no Docker, no broker: exercises the pure helpers — desired-mode
normalization (must mirror install.sh _harden_openappsec_desired_mode:
only explicit 'detect-learn' shadows; anything else defaults to 'prevent')
and strict top-level mode extraction (must never match override-mode or
response-code-only lines).
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
    def test_detect_learn_shadows(self):
        self.assertEqual(normalize_desired_mode("detect-learn"), "detect-learn")

    def test_anything_else_enforces_prevent(self):
        for raw in ("prevent", " prevent ", "", None, "DETECT-LEARN",
                    "enforce", "true"):
            self.assertEqual(normalize_desired_mode(raw), "prevent")

    def test_whitespace_padded_detect_learn_shadows(self):
        # Parity with install.sh (tr -d '[:space:]'): padding is stripped.
        self.assertEqual(normalize_desired_mode(" detect-learn "), "detect-learn")


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
