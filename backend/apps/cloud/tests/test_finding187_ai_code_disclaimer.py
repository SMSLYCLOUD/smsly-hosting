# pylint: disable=invalid-name
"""Regression tests for Finding #187 (``_AI_CODE_DISCLAIMER`` constant).

The codebase analyzer ships the file/route/model metadata of a
customer repo to external AI providers. The fix introduces a
module-level ``_AI_CODE_DISCLAIMER`` string that is prepended to
every prompt built by ``_generate_ai_summary`` so the provider is
explicitly told the payload is structural metadata only and must
not infer, fabricate, or repeat code, secrets, or private
identifiers.
"""
from unittest.mock import patch

from apps.cloud.views import code_analysis as vca
from django.test import SimpleTestCase


class AICodeDisclaimerConstantTests(SimpleTestCase):
    def test_module_level_constant_is_defined(self):
        self.assertTrue(hasattr(vca, "_AI_CODE_DISCLAIMER"))
        self.assertIsInstance(vca._AI_CODE_DISCLAIMER, str)
        self.assertGreater(len(vca._AI_CODE_DISCLAIMER), 20)

    def test_disclaimer_mentions_no_secrets(self):
        text = vca._AI_CODE_DISCLAIMER.lower()
        self.assertIn("secret", text)
        self.assertIn("metadata", text)

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_disclaimer_is_prepended_to_prompt(self, mock_ask):
        mock_ask.return_value = ("summary", "stub")
        analysis = {
            "nodes": [
                {
                    "id": "file-1",
                    "type": "file",
                    "data": {"path": "src/example.py", "size": 100},
                },
                {
                    "id": "route-1",
                    "type": "route",
                    "data": {"label": "/api/health"},
                },
                {
                    "id": "model-1",
                    "type": "model",
                    "data": {"name": "User"},
                },
            ],
            "tech_stack": ["python"],
            "stats": {
                "files": 1, "lines": 100, "directories": 1,
                "languages": {"python": 100},
            },
        }
        vca._generate_ai_summary(analysis)
        prompt = mock_ask.call_args.kwargs["prompt"]
        self.assertTrue(
            prompt.startswith(vca._AI_CODE_DISCLAIMER),
            "Prompt must start with the AI code disclaimer constant",
        )

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_disclaimer_present_in_full_prompt(self, mock_ask):
        mock_ask.return_value = ("summary", "stub")
        analysis = {
            "nodes": [
                {
                    "id": "file-1",
                    "type": "file",
                    "data": {"path": "src/example.py", "size": 100},
                },
            ],
            "tech_stack": ["python"],
            "stats": {
                "files": 1, "lines": 100, "directories": 1,
                "languages": {"python": 100},
            },
        }
        vca._generate_ai_summary(analysis)
        prompt = mock_ask.call_args.kwargs["prompt"]
        self.assertIn(vca._AI_CODE_DISCLAIMER.rstrip(), prompt)
