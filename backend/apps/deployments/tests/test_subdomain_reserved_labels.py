# pylint: disable=invalid-name
"""
Regression tests for Issue 61 (subdomain SUBDOMAIN_RE does not
block reserved labels like ``admin``).

Before the fix, any user could reserve ``admin``, ``api``,
``platform``, etc. as their own tunnel subdomain. With the
``smsly.cloud`` base domain, that produces
``admin.smsly.cloud`` — colliding with platform routes.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views.subdomains import RESERVED_LABELS

User = get_user_model()


class SubdomainReservedLabelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='reserved-user', password='123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = '/api/v1/subdomains/'

    def test_reserved_label_blocklist_is_non_empty(self):
        # The constant must exist and at least contain the
        # obvious platform-critical labels.
        self.assertIn('admin', RESERVED_LABELS)
        self.assertIn('api', RESERVED_LABELS)
        self.assertIn('platform', RESERVED_LABELS)

    def test_reserved_label_admin_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'admin'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('reserved', str(resp.data).lower())

    def test_reserved_label_api_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'api'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_reserved_label_platform_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'platform'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_reserved_label_dashboard_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'dashboard'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_reserved_label_is_accepted(self):
        # Sanity: a normal, non-reserved label still works.
        resp = self.client.post(
            self.url, {'subdomain': 'myapp'}, format='json',
        )
        self.assertEqual(resp.status_code, 201)

    def test_reserved_label_check_is_case_insensitive(self):
        # The user could try ``Admin`` or ``ADMIN`` — the
        # blocklist must catch all cases.
        for variant in ('Admin', 'ADMIN', 'aDmIn'):
            resp = self.client.post(
                self.url, {'subdomain': variant}, format='json',
            )
            self.assertEqual(
                resp.status_code, 400,
                f"Expected reserved rejection for {variant!r}, "
                f"got {resp.status_code}: {resp.data}",
            )
