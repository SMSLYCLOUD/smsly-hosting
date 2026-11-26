"""Unit tests for ApiTrailingSlashNormalizeMiddleware."""

import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

BACKEND_DIR = str(Path(__file__).resolve().parents[1] / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class _DummyResponse:
    def __init__(self, status=200):
        self.status_code = status
        self.headers = {}


class _DummyRequest:
    def __init__(self, path, secure=True, forwarded_proto=""):
        self.path = path
        self.path_info = path
        self.urlconf = None
        self._secure = secure
        self.headers = {"X-Forwarded-Proto": forwarded_proto} if forwarded_proto else {}

    def is_secure(self):
        return self._secure


def _load_middleware_cls():
    try:
        from apps.core.middleware.trailing_slash import ApiTrailingSlashNormalizeMiddleware
        return ApiTrailingSlashNormalizeMiddleware, None
    except ModuleNotFoundError:
        fake_django = ModuleType("django")
        fake_urls = ModuleType("django.urls")
        fake_urls.is_valid_path = lambda path, urlconf=None: False
        fake_django.urls = fake_urls
        with patch.dict(sys.modules, {"django": fake_django, "django.urls": fake_urls}):
            import importlib.util

            mod_path = Path(BACKEND_DIR) / "apps" / "core" / "middleware" / "trailing_slash.py"
            spec = importlib.util.spec_from_file_location("trailing_slash_test_mod", mod_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.ApiTrailingSlashNormalizeMiddleware, mod


class TestApiTrailingSlashNormalizeMiddleware(unittest.TestCase):
    def test_rewrites_slashless_api_path_and_sets_cache_clear_on_https(self):
        mw_cls, mod = _load_middleware_cls()
        seen_paths = []

        def get_response(req):
            seen_paths.append((req.path, req.path_info))
            return _DummyResponse(200)

        mw = mw_cls(get_response)
        req = _DummyRequest("/api/v1/dashboard/overview", secure=True)

        target_mod = mod if mod is not None else sys.modules["apps.core.middleware.trailing_slash"]
        with patch.object(
            target_mod,
            "is_valid_path",
            side_effect=lambda p, urlconf=None: p == "/api/v1/dashboard/overview/",
        ):
            resp = mw(req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            seen_paths,
            [("/api/v1/dashboard/overview/", "/api/v1/dashboard/overview/")],
        )
        self.assertEqual(resp.headers["Cache-Control"], "no-store, no-cache, must-revalidate")
        self.assertEqual(resp.headers.get("Clear-Site-Data"), '"cache"')

    def test_forwarded_proto_https_also_sets_clear_site_data(self):
        mw_cls, mod = _load_middleware_cls()
        mw = mw_cls(lambda req: _DummyResponse(200))
        req = _DummyRequest("/auth/user", secure=False, forwarded_proto="https")

        target_mod = mod if mod is not None else sys.modules["apps.core.middleware.trailing_slash"]
        with patch.object(
            target_mod,
            "is_valid_path",
            side_effect=lambda p, urlconf=None: p == "/auth/user/",
        ):
            resp = mw(req)

        self.assertEqual(req.path_info, "/auth/user/")
        self.assertEqual(resp.headers.get("Clear-Site-Data"), '"cache"')

    def test_slashed_api_path_untouched_without_clear_site_data(self):
        mw_cls, mod = _load_middleware_cls()
        seen_paths = []

        def get_response(req):
            seen_paths.append((req.path, req.path_info))
            return _DummyResponse(200)

        mw = mw_cls(get_response)
        req = _DummyRequest("/api/v1/dashboard/overview/", secure=True)

        target_mod = mod if mod is not None else sys.modules["apps.core.middleware.trailing_slash"]
        with patch.object(
            target_mod,
            "is_valid_path",
            side_effect=lambda p, urlconf=None: True,
        ):
            resp = mw(req)

        self.assertEqual(
            seen_paths,
            [("/api/v1/dashboard/overview/", "/api/v1/dashboard/overview/")],
        )
        self.assertNotIn("Clear-Site-Data", resp.headers)

    def test_non_api_path_untouched(self):
        mw_cls, mod = _load_middleware_cls()
        mw = mw_cls(lambda req: _DummyResponse(200))
        req = _DummyRequest("/dashboard", secure=True)

        target_mod = mod if mod is not None else sys.modules["apps.core.middleware.trailing_slash"]
        with patch.object(
            target_mod,
            "is_valid_path",
            side_effect=lambda p, urlconf=None: p == "/dashboard/",
        ):
            resp = mw(req)

        self.assertEqual(req.path_info, "/dashboard")
        self.assertNotIn("Clear-Site-Data", resp.headers)


if __name__ == "__main__":
    unittest.main()
