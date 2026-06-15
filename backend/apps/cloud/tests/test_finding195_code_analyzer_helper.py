# pylint: disable=invalid-name
"""Tests for the shared ``code_analyzer`` helper (Issue 195).

``cloud.views.analyze_repo`` and ``cloud.views_code_analysis.analyze_codebase``
both walk a cloned repo with a 50 MB total-byte cap. This test pins
the shared ``iter_repo_files`` / ``check_repo_size`` helpers that
both views now use.
"""
import os
import tempfile

from django.test import SimpleTestCase

from apps.cloud.services.code_analyzer import (
    MAX_TOTAL_BYTES,
    check_repo_size,
    iter_repo_files,
    walk_repo_with_cap,
)


def _write_files(root, files):
    for relpath, size in files:
        full = os.path.join(root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("x" * size)


class CodeAnalyzerHelperTests(SimpleTestCase):
    def test_default_total_byte_cap_is_50mb(self):
        self.assertEqual(MAX_TOTAL_BYTES, 50 * 1024 * 1024)

    def test_iter_repo_files_yields_relpath_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a.py", 100), ("sub/b.py", 200)])
            results = list(iter_repo_files(tmp))
            rel_paths = sorted(rel for _abs, rel, _size in results)
            sizes = sorted(size for _abs, _rel, size in results)
            self.assertEqual(rel_paths, ["a.py", os.path.join("sub", "b.py")])
            self.assertEqual(sizes, [100, 200])

    def test_iter_repo_files_respects_max_total_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("big1.bin", 60_000), ("big2.bin", 60_000)])
            results = list(iter_repo_files(tmp, max_total_bytes=80_000))
            self.assertEqual(len(results), 1)

    def test_check_repo_size_sums_under_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a", 1_000), ("b", 2_000), ("c", 3_000)])
            self.assertEqual(check_repo_size(tmp), 6_000)

    def test_check_repo_size_caps_at_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a", 1_000), ("b", 1_000_000)])
            self.assertEqual(
                check_repo_size(tmp, max_total_bytes=10_000),
                1_000,
            )

    def test_iter_repo_files_yields_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("z.py", 50)])
            results = list(iter_repo_files(tmp))
            self.assertEqual(len(results), 1)
            abs_path, rel_path, size = results[0]
            self.assertTrue(os.path.isabs(abs_path))
            self.assertEqual(rel_path, "z.py")
            self.assertEqual(size, 50)

    def test_walk_repo_with_cap_signals_over_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a", 1_000), ("b", 1_000_000)])
            walk = walk_repo_with_cap(tmp, max_total_bytes=10_000)
            self.assertTrue(walk.capped)
            self.assertEqual(walk.total_bytes, 1_000)

    def test_walk_repo_with_cap_under_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_files(tmp, [("a", 100), ("b", 200)])
            walk = walk_repo_with_cap(tmp, max_total_bytes=10_000)
            self.assertFalse(walk.capped)
            self.assertEqual(walk.total_bytes, 300)
            self.assertEqual(walk.file_count, 2)
