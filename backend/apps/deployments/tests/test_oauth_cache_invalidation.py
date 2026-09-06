# pylint: disable=invalid-name
"""
Regression tests for Issue 23 (oauth_credentials cache invalidation).

After a superuser updates an OAuth provider's credentials via the
oauth_credentials POST endpoint, the relevant cache keys must be
invalidated AND the in-process allauth provider registry must be
cleared so the next OAuth flow picks up the new values.
"""

import unittest
from collections import OrderedDict
from typing import Any
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

try:
    from allauth.socialaccount.models import SocialApp
except ImportError:
    SocialApp: Any = None

try:
    from django.contrib.sites.models import Site
except ImportError:
    Site: Any = None

try:
    from allauth.socialaccount import providers as allauth_providers
except ImportError:
    allauth_providers = None


@unittest.skipIf(SocialApp is None, "allauth not installed")
@unittest.skipIf(Site is None, "sites not installed")
@unittest.skipIf(allauth_providers is None, "allauth providers not installed")
class OAuthCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(
            username="oauth-admin",
            email="oauth-admin@example.com",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = "/api/v1/oauth/credentials/"

    def test_post_invalidates_cache_and_registry(self):
        # Patch the SocialApp save signal so we can count the
        # number of times the post_save receiver fires. The
        # receiver is registered at module import time, so we
        # spy on it through the cache delete side-effect.
        from apps.core.views import oauth as views_oauth
        original_delete = views_oauth.cache.delete
        delete_calls = []

        def _spy_delete(key, *args, **kwargs):
            delete_calls.append(key)
            return original_delete(key, *args, **kwargs)

        with patch.object(views_oauth.cache, "delete", side_effect=_spy_delete):
            resp = self.client.post(
                self.url,
                {
                    "github": {
                        "client_id": "id-1",
                        "client_secret": "sec-1",
                    },
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        # At least one cache key starting with "social_app:github:"
        # should have been deleted.
        self.assertTrue(
            any(k.startswith("social_app:github:") for k in delete_calls),
            f"No social_app:github: cache delete in {delete_calls}",
        )

    def test_receiver_clears_provider_registry_and_cache(self):
        class _Stub:
            id = "github"
            name = "Stub"

        allauth_providers.registry.provider_map = OrderedDict([("github", _Stub)])
        allauth_providers.registry.loaded = True

        cache.set("social_app:github:42", "stale", timeout=60)

        from apps.core.views.oauth import _invalidate_social_app_cache

        instance = SocialApp(
            id=42, provider="github", client_id="id", secret="sec", name="GitHub",
        )
        _invalidate_social_app_cache(sender=SocialApp, instance=instance)

        self.assertIsNone(cache.get("social_app:github:42"))
        # The receiver wipes the in-process registry AND immediately
        # reloads it: request-time lookups (registry.get_class, which
        # does NOT reload on its own) must keep working in the worker
        # that handled the save. A wipe without reload broke every
        # social login in that worker until restart.
        self.assertTrue(allauth_providers.registry.loaded)
        self.assertGreater(len(allauth_providers.registry.provider_map), 0)
        github_cls = allauth_providers.registry.get_class("github")
        self.assertIsNotNone(github_cls)
        self.assertNotEqual(github_cls, _Stub)
        self.assertEqual(github_cls.id, "github")

