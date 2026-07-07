"""
Update container_registry_url default from 127.0.0.1:5000 to registry:5000.

Internal registry access should use Docker DNS (registry:5000) rather
than loopback (127.0.0.1:5000) so inter-container pushes resolve correctly
on the smsly-net overlay.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0133_add_smtp_fields_to_platformconfig'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='container_registry_url',
            field=models.CharField(
                blank=True,
                default='registry:5000',
                help_text='Container registry URL (e.g. registry:5000 for internal, or docker.io/ghcr.io for external)',
                max_length=255,
            ),
        ),
    ]
