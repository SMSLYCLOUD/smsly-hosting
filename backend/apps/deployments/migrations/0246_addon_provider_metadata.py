# Addon provider bookkeeping (mesh forward ports, remote mesh-backing
# marker). Minimal: only the new field — sibling AlterField drift from
# other branches stays out of this migration.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0245_sops_backend'),
    ]

    operations = [
        migrations.AddField(
            model_name='addon',
            name='provider_metadata',
            field=models.JSONField(blank=True, default=dict, help_text="Provider bookkeeping: mesh forward ports ('mesh_forward_port'), remote mesh-backing marker ('mesh_backed'), never credentials."),
        ),
    ]
