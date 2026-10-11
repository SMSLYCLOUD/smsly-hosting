"""Trulay promos (dashboard cross-sell banner) config surface.

Covers:
  1. _PC_FIELDS accepts PROMOS_ENABLED / PROMOS_ROTATE_SECONDS / PROMOS_ITEMS.
  2. _get_public_promos is fail-closed (hidden when the DB is unavailable).
  3. PATCH rejects non-list items, missing id/title/href, and non-https href.
  4. PATCH clamps PROMOS_ROTATE_SECONDS to 5-60.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.views.system import SystemConfigView

User = get_user_model()


class PromoFieldMappingTests(SimpleTestCase):
    def test_promo_fields_registered(self):
        self.assertIn('PROMOS_ENABLED', SystemConfigView._PC_FIELDS)
        self.assertIn('PROMOS_ROTATE_SECONDS', SystemConfigView._PC_FIELDS)
        self.assertIn('PROMOS_ITEMS', SystemConfigView._PC_FIELDS)
        field, cast = SystemConfigView._PC_FIELDS['PROMOS_ENABLED']
        self.assertEqual(field, 'promos_enabled')
        self.assertIs(cast, bool)

    def test_public_promos_fail_closed(self):
        view = SystemConfigView()
        with mock.patch(
            'apps.core.views.system.PlatformConfig.objects.get_or_create',
            side_effect=Exception('no db'),
        ):
            result = view._get_public_promos()
        self.assertEqual(result, {
            'PROMOS_ENABLED': False, 'PROMOS_ITEMS': [], 'PROMOS_ROTATE_SECONDS': 8,
        })


class PromoPatchTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = SystemConfigView.as_view()
        self.admin = User.objects.create_superuser(
            username="promoadmin", password="x", email="p@x.io",
        )

    def _patch(self, data):
        request = self.factory.patch('/system/config/', data, format='json')
        force_authenticate(request, user=self.admin)
        return self.view(request)

    def test_rejects_non_list_items(self):
        response = self._patch({'PROMOS_ITEMS': {'id': 'x'}})
        self.assertEqual(response.status_code, 400)

    def test_rejects_missing_fields(self):
        response = self._patch({'PROMOS_ITEMS': [{'id': 'x', 'title': 'X'}]})
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_https_href(self):
        response = self._patch({'PROMOS_ITEMS': [
            {'id': 'x', 'title': 'X', 'href': 'http://evil.example/x'},
        ]})
        self.assertEqual(response.status_code, 400)

    def test_accepts_valid_items_and_clamps_rotate(self):
        response = self._patch({
            'PROMOS_ENABLED': True,
            'PROMOS_ROTATE_SECONDS': 500,
            'PROMOS_ITEMS': [
                {'id': 'sms-apis', 'title': 'APIs',
                 'href': 'https://Trulay.co/?utm_content=sms-apis'},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['PROMOS_ENABLED'])
        self.assertEqual(response.data['PROMOS_ROTATE_SECONDS'], 60)
        self.assertEqual(len(response.data['PROMOS_ITEMS']), 1)
        self.assertEqual(response.data['PROMOS_ITEMS'][0]['cta'], 'Learn more')
