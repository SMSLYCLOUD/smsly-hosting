import os
import re

from django.test import SimpleTestCase


CLOUD_VIEWS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "views.py",
))
CLOUD_CODE_ANALYSIS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "views_code_analysis.py",
))


class OsWalkFollowlinksSourceTests(SimpleTestCase):
    def test_cloud_views_analyze_repo_uses_followlinks_false(self):
        with open(CLOUD_VIEWS, "r", encoding="utf-8") as fh:
            content = fh.read()
        match = re.search(
            r"def analyze_repo.*?os\.walk\(([^)]+)\)",
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "Could not find os.walk in analyze_repo")
        self.assertIn("followlinks=False", match.group(1))

    def test_code_analysis_analyze_codebase_uses_followlinks_false(self):
        with open(CLOUD_CODE_ANALYSIS, "r", encoding="utf-8") as fh:
            content = fh.read()
        match = re.search(
            r"os\.walk\(([^)]+)\)",
            content,
        )
        self.assertIsNotNone(match, "Could not find os.walk in views_code_analysis")
        self.assertIn("followlinks=False", match.group(1))
