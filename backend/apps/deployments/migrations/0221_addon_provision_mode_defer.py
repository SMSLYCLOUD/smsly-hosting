# 2026-09-18: provision_mode '' means "defer to the platform default"
# (PlatformConfig.postgres_shared_addons_default) so the dashboard can offer
# an explicit Shared / Individual / Platform-default choice. Previously the
# '' state did not exist: fresh rows defaulted to 'container', which the
# resolver treated as "defer". The resolver now treats 'container' as an
# explicit force-container, so never-provisioned 'container' rows are moved
# to '' to preserve their old defer-to-default behaviour. Provisioned rows
# are untouched (sticky URL/container checks already pin them).

from django.db import migrations, models


def _defer_unprovisioned(apps, schema_editor):
    Addon = apps.get_model('deployments', 'Addon')
    Addon.objects.filter(
        provision_mode='container',
        status__in=['PROVISIONING', 'FAILED'],
    ).update(provision_mode='')


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0220_addon_provision_mode_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='addon',
            name='provision_mode',
            field=models.CharField(blank=True, choices=[('container', 'Dedicated container'), ('shared', 'Logical DB on shared server')], default='', help_text="How this addon is hosted: 'shared' forces a logical database on the shared Postgres server, 'container' forces a dedicated container, '' defers to the platform default (PlatformConfig.postgres_shared_addons_default). Only POSTGRES honours this; other types always use dedicated containers.", max_length=20),
        ),
        migrations.RunPython(_defer_unprovisioned, _noop),
    ]
