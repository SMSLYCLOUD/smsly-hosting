"""Autoscale headroom: bigger default ceilings + lift existing rows.

The engine now elongates by 2 (high) / 4 (critical) replicas per tick,
but existing services are capped at the old max_replicas=3 default in
the DB — the new steps would clamp to a headroom of 2. This migration:

* changes the Service.max_replicas field default 3 -> 8,
* lifts existing rows sitting exactly on the old default (3 -> 8),
* changes the PlatformConfig.scale_max_replicas default 5 -> 12 and
  lifts rows sitting on the old default.

Rows an operator deliberately tuned to other values are untouched.
Per-service resource tiers (cpu/memory) move in code
(default_service_resources) and need no schema change.
"""
from django.core.validators import MinValueValidator
from django.db import migrations, models


OLD_SERVICE_MAX = 3
NEW_SERVICE_MAX = 8
OLD_PLATFORM_MAX = 5
NEW_PLATFORM_MAX = 12


def lift_ceilings_forward(apps, schema_editor):
    Service = apps.get_model('deployments', 'Service')
    Service.objects.filter(max_replicas=OLD_SERVICE_MAX).update(
        max_replicas=NEW_SERVICE_MAX
    )
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    PlatformConfig.objects.filter(
        scale_max_replicas=OLD_PLATFORM_MAX
    ).update(scale_max_replicas=NEW_PLATFORM_MAX)


def lift_ceilings_backward(apps, schema_editor):
    Service = apps.get_model('deployments', 'Service')
    Service.objects.filter(max_replicas=NEW_SERVICE_MAX).update(
        max_replicas=OLD_SERVICE_MAX
    )
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    PlatformConfig.objects.filter(
        scale_max_replicas=NEW_PLATFORM_MAX
    ).update(scale_max_replicas=OLD_PLATFORM_MAX)


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0213_crowdsec_cf_bouncer'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='max_replicas',
            field=models.IntegerField(default=8, validators=[MinValueValidator(1)]),
        ),
        migrations.AlterField(
            model_name='platformconfig',
            name='scale_max_replicas',
            field=models.PositiveIntegerField(
                default=12,
                help_text='Maximum number of replica containers allowed per service',
            ),
        ),
        migrations.RunPython(lift_ceilings_forward, lift_ceilings_backward),
    ]
