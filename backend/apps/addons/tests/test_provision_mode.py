"""provision_mode semantics: explicit choice wins, '' defers to default.

- 'shared' forces the shared logical pool.
- 'container' forces a dedicated container for fresh addons.
- '' follows PlatformConfig.postgres_shared_addons_default.
- The mode cannot change once the addon is provisioned (would strand data).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.addons.services.addon_provisioner import AddonProvisioner
from apps.addons.views.crud import AddonSerializer
from apps.deployments.models import Addon, Service
from apps.deployments.models.platform import PlatformConfig

User = get_user_model()


class ProvisionModeSerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="provmodemode", password="x")
        self.service = Service.objects.create(name="modsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(service=self.service, name="pg", addon_type="POSTGRES")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def test_create_accepts_all_modes(self):
        for mode in ('', 'shared', 'container'):
            s = AddonSerializer(data={
                'service': str(self.service.id),
                'name': f'pg-{mode or "default"}',
                'addon_type': 'POSTGRES',
                'provision_mode': mode,
            })
            self.assertTrue(s.is_valid(), f"mode {mode!r}: {s.errors}")

    def test_create_rejects_unknown_mode(self):
        s = AddonSerializer(data={
            'service': str(self.service.id),
            'name': 'pg-bad',
            'addon_type': 'POSTGRES',
            'provision_mode': 'whatever',
        })
        self.assertFalse(s.is_valid())

    def test_update_allowed_before_provisioning(self):
        addon = self._addon(provision_mode='')
        s = AddonSerializer(
            instance=addon, data={'provision_mode': 'shared'}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)

    def test_update_rejected_after_provisioning(self):
        addon = self._addon(
            provision_mode='shared', status=Addon.Status.ACTIVE,
            connection_url='postgresql://x',
        )
        s = AddonSerializer(
            instance=addon, data={'provision_mode': 'container'}, partial=True)
        self.assertFalse(s.is_valid())


class ResolvePostgresModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="resolvermode", password="x")
        self.service = Service.objects.create(name="resolvsvc", owner=self.user)
        self.provisioner = mock.Mock(spec=AddonProvisioner)
        self.provisioner._container_status.side_effect = Exception('no docker')

    def _addon(self, **kwargs):
        defaults = dict(service=self.service, name="pg", addon_type="POSTGRES")
        defaults.update(kwargs)
        return Addon(**defaults)

    def _resolve(self, addon):
        return AddonProvisioner._resolve_postgres_mode(
            self.provisioner, addon, 'pg-container')

    def test_explicit_shared_wins_over_default_off(self):
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={'postgres_shared_addons_default': False})
        self.assertEqual(self._resolve(self._addon(provision_mode='shared')), 'shared')

    def test_explicit_container_wins_over_default_on(self):
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={'postgres_shared_addons_default': True})
        self.assertEqual(
            self._resolve(self._addon(provision_mode='container')), 'container')

    def test_blank_defers_to_default(self):
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={'postgres_shared_addons_default': True})
        self.assertEqual(self._resolve(self._addon(provision_mode='')), 'shared')
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={'postgres_shared_addons_default': False})
        self.assertEqual(self._resolve(self._addon(provision_mode='')), 'container')

    def test_existing_url_stays_container(self):
        PlatformConfig.objects.update_or_create(
            pk=1, defaults={'postgres_shared_addons_default': True})
        addon = self._addon(provision_mode='', connection_url='postgresql://x')
        self.assertEqual(self._resolve(addon), 'container')
