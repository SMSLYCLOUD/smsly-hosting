"""
Add Cosign image signing and backup encryption fields to PlatformConfig.

- cosign_enabled: toggle Cosign signing after build
- cosign_require_verification: enforce signature verification before deploy
- backup_require_encryption: require encrypted backups
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0134_update_container_registry_url_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='cosign_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Sign container images with Cosign after build. '
                          'Non-fatal if Cosign is not installed.',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='cosign_require_verification',
            field=models.BooleanField(
                default=False,
                help_text='Require Cosign signature verification before deploying images. '
                          'Deployments fail if the image is unsigned or verification fails.',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='backup_require_encryption',
            field=models.BooleanField(
                default=False,
                help_text='Require encryption for server backups. '
                          'Auto-enabled in production (DEBUG=False) via settings.',
            ),
        ),
    ]
