# pylint: disable=invalid-name
"""
Unit tests for AddonProvisioner idempotency.

These tests ensure addon provisioning does not rotate passwords or drift
credentials on retries when a persistent volume is re-used.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Region, Service
from apps.deployments.models_addons import Addon
from services.addon_provisioner import AddonProvisioner


User = get_user_model()


class AddonProvisionerIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='addonprov', password='password')
        self.region = Region.objects.create(name='Test Region', slug='test-region')
        self.service = Service.objects.create(
            name='SMSLY-MARKETER',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT',
        )

    def test_existing_url_running_container_does_not_rotate_password(self):
        addon = Addon.objects.create(
            service=self.service,
            name='postgres-SMSLY-MARKETER',
            addon_type='POSTGRES',
            status='ACTIVE',
            connection_url='postgresql://u:p@postgres-smsly-marketer:5432/db',
        )
        prov = AddonProvisioner()

        with patch.object(prov, '_ensure_network'), \
            patch.object(prov, '_container_status', return_value=('abc123def456', True)), \
            patch.object(prov, '_wait_for_health'), \
            patch('services.addon_provisioner.secrets.token_urlsafe', side_effect=AssertionError("rotated")):
            cid, url = prov.provision(addon)

        self.assertEqual(cid, 'abc123def456')
        self.assertEqual(url, addon.connection_url)

    def test_existing_url_missing_container_recreates_with_same_credentials(self):
        addon = Addon.objects.create(
            service=self.service,
            name='postgres-SMSLY-MARKETER',
            addon_type='POSTGRES',
            status='ACTIVE',
            connection_url='postgresql://postgres_SMSLY_MARKETER:secret@postgres-smsly-marketer:5432/postgres_SMSLY_MARKETER',
        )
        prov = AddonProvisioner()
        expected_container_name = f"smsly-addon-postgres-{addon.id}"

        with patch.object(prov, '_ensure_network'), \
            patch.object(prov, '_container_status', return_value=(None, False)), \
            patch.object(prov, '_provision_postgres', return_value=('newcid', 'ignored')) as mock_prov, \
            patch('services.addon_provisioner.secrets.token_urlsafe', side_effect=AssertionError("rotated")):
            cid, url = prov.provision(addon)

        self.assertEqual(cid, 'newcid')
        self.assertEqual(url, addon.connection_url)

        args, kwargs = mock_prov.call_args
        self.assertEqual(args[0], expected_container_name)
        self.assertEqual(args[1], 'secret')
        self.assertEqual(args[2], 5432)
        self.assertEqual(args[3], 'postgres-smsly-marketer')
        self.assertEqual(kwargs.get('db_user'), 'postgres_SMSLY_MARKETER')
        self.assertEqual(kwargs.get('db_name'), 'postgres_SMSLY_MARKETER')

