# 2026-09-18: pooler_routed pins a shared POSTGRES addon to the
# pgcat-tenants pooler. Set at provision/migration time only; existing rows
# default False (direct connections — current reality, never moved).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0222_platformconfig_tenant_pooling'),
    ]

    operations = [
        migrations.AddField(
            model_name='addon',
            name='pooler_routed',
            field=models.BooleanField(default=False, help_text='Shared POSTGRES addon dials through the pgcat-tenants pooler (set at provision/migration time; sticky — toggling tenant_pooling_enabled never moves it back).'),
        ),
    ]
