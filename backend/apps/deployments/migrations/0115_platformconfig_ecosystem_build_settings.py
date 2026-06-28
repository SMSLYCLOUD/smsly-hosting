from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0114_cloud_backup_tracking'),
    ]

    operations = [
        # ── Ecosystem build settings ─────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='ecosystem_max_concurrent_builds',
            field=models.PositiveIntegerField(
                default=2,
                help_text='Maximum concurrent ecosystem builds (overrides max_concurrent_builds for ecosystem deploys)',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='ecosystem_build_stagger_seconds',
            field=models.PositiveIntegerField(
                default=30,
                help_text='Seconds between each build start within an ecosystem wave (prevents OOM)',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='ecosystem_default_wave_size',
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text='Default number of services per ecosystem deploy wave',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='ecosystem_wave_recheck_seconds',
            field=models.PositiveIntegerField(
                default=15,
                help_text='Seconds between wave completion rechecks',
            ),
        ),
        # ── Billing ──────────────────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='billing_currency',
            field=models.CharField(blank=True, default='USD', max_length=10),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='billing_pro_amount',
            field=models.CharField(blank=True, default='29.00', max_length=20),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='billing_pro_period_days',
            field=models.PositiveIntegerField(default=30),
        ),
        # ── SMSLY Platform URLs ──────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_sms_api_url',
            field=models.URLField(blank=True, default='http://smsly-sms:8000/api/v1', max_length=300),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_voice_api_url',
            field=models.URLField(blank=True, default='http://smsly-voice:8000/api/v1', max_length=300),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_platform_api_url',
            field=models.URLField(blank=True, default='http://smsly-platform-api:8000/api/v1', max_length=300),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_internal_api_key',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
        # ── Alerting ─────────────────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='alert_phone_number',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='critical_alert_phone',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='notify_on_success',
            field=models.BooleanField(default=False),
        ),
        # ── Container Registry ───────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='container_registry_url',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='registry_user',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='registry_password',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
        # ── Observability ────────────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='sentry_dsn',
            field=models.CharField(blank=True, default='', max_length=300),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='sentry_traces_sample_rate',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='sentry_profiles_sample_rate',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='sentry_environment',
            field=models.CharField(blank=True, default='production', max_length=50),
        ),
        # ── Feature Flags ────────────────────────────────────────────────
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_disable_tier_gates',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='enable_legacy_tunnel_api',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='smsly_strict_ssh_host_key_check',
            field=models.BooleanField(default=False),
        ),
    ]
