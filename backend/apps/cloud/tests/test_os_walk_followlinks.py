import os
import re

from django.test import SimpleTestCase

CLOUD_CODE_ANALYZER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "services", "code_analyzer.py",
))
CLOUD_CODE_ANALYSIS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "views", "code_analysis.py",
))


class OsWalkFollowlinksSourceTests(SimpleTestCase):
    def test_cloud_views_analyze_repo_uses_followlinks_false(self):
        with open(CLOUD_CODE_ANALYZER, encoding="utf-8") as fh:
            content = fh.read()
        matches = re.findall(
            r"os\.walk\(([^)]+)\)",
            content,
        )
        self.assertTrue(matches, "Could not find os.walk in code_analyzer")
        for args in matches:
            self.assertIn("followlinks=False", args)

    def test_code_analysis_analyze_codebase_uses_followlinks_false(self):
        with open(CLOUD_CODE_ANALYSIS, encoding="utf-8") as fh:
            content = fh.read()
        match = re.search(
            r"os\.walk\(([^)]+)\)",
            content,
        )
        self.assertIsNotNone(match, "Could not find os.walk in views/code_analysis.py")
        self.assertIn("followlinks=False", match.group(1))
