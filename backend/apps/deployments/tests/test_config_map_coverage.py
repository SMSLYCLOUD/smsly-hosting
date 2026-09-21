"""_CONFIG_MAP must cover every field the installer reads via get_config_value.

Regression: lib/harden_crowdsec.sh reads crowdsec_cf_enabled through
PlatformConfig.get_config_value, which returns the default for unmapped
fields — the UI toggle wrote True to the DB while every installer run
saw disabled, so the Cloudflare edge bouncer never started.
"""
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.deployments.models.platform import PlatformConfig


class ConfigMapCoverageTests(SimpleTestCase):
    # Fields read via get_config_value outside Django (installer shell,
    # management commands). Keep in sync with lib/harden_crowdsec.sh
    # _harden_crowdsec_cf_value call sites.
    SHELL_READ_FIELDS = {
        'crowdsec_cf_enabled',
        'crowdsec_cf_api_token',
        'crowdsec_cf_account_id',
        'crowdsec_cf_action',
        'crowdsec_cf_bouncer_key',
    }

    def test_shell_read_fields_are_mapped(self):
        missing = [
            f for f in self.SHELL_READ_FIELDS
            if f not in PlatformConfig._CONFIG_MAP
        ]
        self.assertEqual(missing, [])


class ConfigMapValueTests(TestCase):
    def test_enabled_true_surfaces_as_truthy_string(self):
        # The shell treats only 'True'/'1' as enabled; a DB True must
        # surface in one of those forms, never ''.
        config = PlatformConfig.load()
        config.crowdsec_cf_enabled = True
        config.save(update_fields=['crowdsec_cf_enabled'])
        PlatformConfig.clear_cache()
        try:
            self.assertIn(
                PlatformConfig.get_config_value('crowdsec_cf_enabled', ''),
                ('True', '1'),
            )
        finally:
            config.crowdsec_cf_enabled = False
            config.save(update_fields=['crowdsec_cf_enabled'])
            PlatformConfig.clear_cache()
