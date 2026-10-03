# SOPS-age secrets backend: vault opt-in flag + age keypair storage.

import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0244_service_edge_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='infisical_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='secrets_age_private_key',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', max_length=2048),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='secrets_age_public_key',
            field=models.CharField(blank=True, default='', max_length=256),
        ),
    ]
