# pylint: disable=invalid-name
import os
import re
import unittest

from django.test import SimpleTestCase


REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
)
API_TS_PATH = os.path.join(
    REPO_ROOT, "frontend", "src", "lib", "api.ts",
)


# TODO(security/Finding#123): If cookie-based auth is ever introduced,
# the hard-coded `withCredentials: true` in `frontend/src/lib/api.ts`
# starts sending the CSRF-relevant cookies to the API on cross-site
# requests.  Re-evaluate CSRF posture and add an explicit `X-CSRF-Token`
# or SameSite=Lax header strategy before flipping auth schemes.


class Finding123ApiWithCredentialsTests(SimpleTestCase):
    def test_api_ts_file_exists(self):
        self.assertTrue(
            os.path.exists(API_TS_PATH),
            f"expected frontend api client at {API_TS_PATH}",
        )

    def test_api_ts_hard_codes_with_credentials_true(self):
        with open(API_TS_PATH, encoding="utf-8") as fh:
            contents = fh.read()
        match = re.search(r"^\s*withCredentials\s*:\s*(\w+)\s*,?\s*$", contents, re.MULTILINE)
        self.assertIsNotNone(
            match,
            "could not find a `withCredentials: <value>` line in api.ts",
        )
        self.assertEqual(
            match.group(1), "true",
            f"expected withCredentials: true, got: {match.group(1)!r}",
        )


if __name__ == "__main__":
    unittest.main()
