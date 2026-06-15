# pylint: disable=invalid-name
"""Tests for ``validate_and_sanitize_path`` rejecting ``$`` (Issue 131).

The original check looked for ``$`` combined with ``{`` and
``}`` (i.e. ``${...}``).  A path with a bare ``$`` (e.g.
``/data/$USER/file``) was allowed.  The fix rejects any path
containing ``$`` regardless of brace context.
"""
from django.test import SimpleTestCase

from apps.deployments.utils import validate_and_sanitize_path


class ValidateAndSanitizePathDollarTests(SimpleTestCase):
    def test_rejects_dollar_alone(self):
        with self.assertRaises(ValueError):
            validate_and_sanitize_path("/data/$USER/file")

    def test_rejects_dollar_with_braces(self):
        with self.assertRaises(ValueError):
            validate_and_sanitize_path("/data/${HOME}/file")

    def test_rejects_dollar_no_braces(self):
        with self.assertRaises(ValueError):
            validate_and_sanitize_path("/data/$foo")

    def test_rejects_trailing_dollar(self):
        with self.assertRaises(ValueError):
            validate_and_sanitize_path("/data/dir$")

    def test_allows_clean_path(self):
        out = validate_and_sanitize_path("/data/work/file.txt")
        self.assertEqual(out, "/data/work/file.txt")

    def test_allows_path_with_dot_in_dirname(self):
        out = validate_and_sanitize_path("/data/.cache/file.txt")
        self.assertEqual(out, "/data/.cache/file.txt")
