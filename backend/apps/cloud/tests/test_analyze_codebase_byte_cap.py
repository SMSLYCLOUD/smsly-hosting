import os
import tempfile
from unittest.mock import patch

from apps.cloud.views import code_analysis as vca
from apps.cloud.views.code_analysis import (
    MAX_TOTAL_BYTES,
    analyze_codebase,
)
from django.test import SimpleTestCase


def _write_files(root, files):
    for relpath, size in files:
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("x" * size)


class AnalyzeCodebaseTotalByteCapTests(SimpleTestCase):
    def test_total_byte_cap_constant_is_50mb(self):
        self.assertEqual(MAX_TOTAL_BYTES, 50 * 1024 * 1024)

    def test_small_repo_analyzed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a.py", 100), ("b.py", 200)])
            result = analyze_codebase(tmp)
            self.assertEqual(result["stats"]["files"], 2)
            self.assertIn("python", result["stats"]["languages"])

    def test_repo_over_50mb_raises(self):
        from rest_framework.exceptions import ValidationError
        with tempfile.TemporaryDirectory() as tmp:
            file_size = 90_000
            files = [(f"src/d{i}.py", file_size) for i in range(600)]
            _write_files(tmp, files)
            with patch.object(vca, "MAX_FILES", 10_000), \
                 patch.object(vca, "MAX_FILE_SIZE", 200_000):
                with self.assertRaises(ValidationError) as ctx:
                    analyze_codebase(tmp)
            self.assertIn("too large", str(ctx.exception).lower())

    def test_repo_exactly_at_cap_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_size = 90_000
            files = [(f"src/d{i}.py", file_size) for i in range(400)]
            _write_files(tmp, files)
            result = analyze_codebase(tmp)
            self.assertEqual(result["stats"]["files"], 400)
