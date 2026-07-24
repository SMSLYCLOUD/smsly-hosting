# pylint: disable=invalid-name
"""Tests for ``_generate_ai_summary`` file-list cap (Issue 199).

The function previously sent the full file list to the AI
provider, potentially exceeding its context window.  The fix
caps the list at 50 files (sorted by size descending) and
includes a ``truncated_count`` in the prompt metadata.
"""
from unittest.mock import patch

from apps.cloud.views.code_analysis import _generate_ai_summary
from django.test import SimpleTestCase


class GenerateAiSummaryFileListCapTests(SimpleTestCase):
    def _build_analysis(self, file_count, file_size=1000):
        return {
            "nodes": [
                {
                    "id": f"file-{i}",
                    "type": "file",
                    "data": {
                        "path": f"src/file_{i:04d}.py",
                        "size": file_size + i,
                    },
                }
                for i in range(file_count)
            ] + [
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
            "tech_stack": ["python", "django"],
            "stats": {
                "files": file_count,
                "lines": 1000,
                "directories": 5,
                "languages": {"python": 800},
            },
        }

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_under_50_files_no_truncation(self, mock_ask):
        mock_ask.return_value = ("summary", "stub")
        analysis = self._build_analysis(20)
        _generate_ai_summary(analysis)
        prompt = mock_ask.call_args.kwargs["prompt"]
        self.assertIn("'truncated_count': 0", prompt)
        self.assertIn("'included_files': 20", prompt)

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_over_50_files_truncates(self, mock_ask):
        mock_ask.return_value = ("summary", "stub")
        analysis = self._build_analysis(200)
        _generate_ai_summary(analysis)
        prompt = mock_ask.call_args.kwargs["prompt"]
        self.assertIn("'truncated_count': 150", prompt)
        self.assertIn("'included_files': 50", prompt)

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_top_50_files_sorted_by_size(self, mock_ask):
        mock_ask.return_value = ("summary", "stub")
        analysis = self._build_analysis(200, file_size=100)
        _generate_ai_summary(analysis)
        prompt = mock_ask.call_args.kwargs["prompt"]
        for i in range(199, 149, -1):
            self.assertIn(f"src/file_{i:04d}.py", prompt)
        for i in range(0, 150):
            self.assertNotIn(f"src/file_{i:04d}.py", prompt)

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_provider_failure_returns_empty(self, mock_ask):
        mock_ask.side_effect = RuntimeError("provider down")
        analysis = self._build_analysis(100)
        result = _generate_ai_summary(analysis)
        self.assertEqual(result, "")
