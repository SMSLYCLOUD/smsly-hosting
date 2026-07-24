# pylint: disable=invalid-name
"""
Unit tests for AddonProvisioner idempotency.

These tests ensure addon provisioning does not rotate passwords or drift
credentials on retries when a persistent volume is re-used.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from apps.addons.services.addon_provisioner import AddonProvisioner

from apps.deployments.models import Region, Service
from apps.deployments.models.addons import Addon

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
            patch('apps.addons.services.addon_provisioner.secrets.token_urlsafe', side_effect=AssertionError("rotated")):
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
            patch('apps.addons.services.addon_provisioner.secrets.token_urlsafe', side_effect=AssertionError("rotated")):
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

    @patch('apps.addons.services.addon_provisioner.subprocess.run')
    def test_kafka_generic_provision_uses_alias_and_extended_health_timeout(self, mock_run):
        mock_run.return_value.stdout = '1234567890abcdef'
        prov = AddonProvisioner()
        kafka_cfg = prov.GENERIC_ADDONS_CONFIG['KAFKA']

        with patch.object(prov, '_wait_for_health') as mock_wait, \
             patch.object(prov, '_wait_for_ready_command') as mock_ready:
            container_id, connection_url = prov._provision_generic(
                'KAFKA',
                'smsly-addon-kafka-1',
                password='unused',
                port=9092,
                alias_name='kafka-myservice',
                config=kafka_cfg,
            )

        self.assertEqual(container_id, '1234567890ab')
        self.assertEqual(connection_url, 'kafka://kafka-myservice:9092')
        mock_wait.assert_called_once_with(
            'smsly-addon-kafka-1',
            9092,
            timeout=120
        )
        mock_ready.assert_called_once_with(
            'smsly-addon-kafka-1',
            kafka_cfg['ready_cmd'],
            timeout=120
        )

        run_cmd = mock_run.call_args[0][0]
        cmd_text = ' '.join(run_cmd)
        self.assertIn(
            '-e KAFKA_CFG_ADVERTISED_LISTENERS=PLAINTEXT://kafka-myservice:9092',
            cmd_text
        )
        self.assertIn(
            '-e KAFKA_CFG_CONTROLLER_QUORUM_VOTERS=1@kafka-myservice:9093',
            cmd_text
        )
        self.assertIn(
            '-e ALLOW_PLAINTEXT_LISTENER=yes',
            cmd_text
        )
        self.assertRegex(
            cmd_text,
            r'-e KAFKA_KRAFT_CLUSTER_ID=[A-Za-z0-9_-]{22}'
        )

    @patch('apps.addons.services.addon_provisioner.subprocess.run')
    def test_all_generic_addons_render_docker_run_commands_and_urls(self, mock_run):
        """Static safety net: every generic addon can render a runnable docker cmd."""
        mock_run.return_value.stdout = 'fedcba0987654321'
        prov = AddonProvisioner()

        for addon_type, cfg in prov.GENERIC_ADDONS_CONFIG.items():
            with self.subTest(addon_type=addon_type):
                with patch.object(prov, '_wait_for_health') as mock_wait:
                    with patch.object(prov, '_wait_for_ready_command') as mock_ready:
                        container_id, connection_url = prov._provision_generic(
                            addon_type=addon_type,
                            container_name=f'smsly-addon-{addon_type.lower()}-1',
                            password='p@ssw0rd',
                            port=cfg['port'],
                            alias_name=f'{addon_type.lower()}-svc',
                            config=cfg,
                        )

                self.assertEqual(container_id, 'fedcba098765')
                self.assertIn('://', connection_url)
                self.assertNotIn('{password}', connection_url)
                self.assertNotIn('{hostname}', connection_url)

                run_cmd = mock_run.call_args[0][0]
                cmd_text = ' '.join(run_cmd)
                self.assertIn('docker run -d', cmd_text)
                self.assertIn(f'--name smsly-addon-{addon_type.lower()}-1', cmd_text)
                self.assertIn(f'--network-alias {addon_type.lower()}-svc', cmd_text)
                self.assertIn(cfg['image'], cmd_text)

                wait_timeout = int(cfg.get('health_timeout', 30))
                mock_wait.assert_called_once_with(
                    f'smsly-addon-{addon_type.lower()}-1',
                    cfg['port'],
                    timeout=wait_timeout
                )
                ready_cmd = str(cfg.get('ready_cmd') or '').strip()
                if ready_cmd:
                    mock_ready.assert_called_once_with(
                        f'smsly-addon-{addon_type.lower()}-1',
                        ready_cmd,
                        timeout=int(cfg.get('ready_timeout', 30))
                    )
                else:
                    mock_ready.assert_not_called()
