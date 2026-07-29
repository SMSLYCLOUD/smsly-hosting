# pylint: disable=invalid-name
"""
Tests for environment variable shortcodes (apps.deployments.services.env_resolver).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.deployments.services.env_resolver import resolve_shortcodes

from apps.deployments.models import Region, Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class EnvShortcodeResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='shortcodetest', password='password')
        self.region = Region.objects.create(name='Test Region', slug='test-region')
        self.service = Service.objects.create(
            name='SMSLY-MARKETER',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT',
        )

        self.addon = Addon.objects.create(
            service=self.service,
            name='postgres-smsly-marketer',
            addon_type='POSTGRES',
            status='ACTIVE',
            connection_url='postgresql://postgres_SMSLY_MARKETER:secret@postgres-smsly-marketer:5432/postgres_SMSLY_MARKETER',
        )

    def test_platform_shortcode_defaults_to_url(self):
        value = "DB={{SMSLY.POSTGRES}}"
        out = resolve_shortcodes(str(self.service.id), value)
        self.assertEqual(out, f"DB={self.addon.connection_url}")

    def test_platform_shortcode_supports_typo_postgress(self):
        value = "{{SMSLY.POSTGRESS.URL}}"
        out = resolve_shortcodes(str(self.service.id), value)
        self.assertEqual(out, self.addon.connection_url)

    def test_platform_shortcode_parses_components(self):
        value = "H={{SMSLY.POSTGRES.HOST}} P={{SMSLY.POSTGRES.PORT}} U={{SMSLY.POSTGRES.USER}}"
        out = resolve_shortcodes(str(self.service.id), value)
        self.assertEqual(out, "H=postgres-smsly-marketer P=5432 U=postgres_SMSLY_MARKETER")

    def test_legacy_shortcode_by_addon_name(self):
        value = "DB={{postgres-smsly-marketer.URL}}"
        out = resolve_shortcodes(str(self.service.id), value)
        self.assertEqual(out, f"DB={self.addon.connection_url}")

