# Generated for infisical_service_token (DB-stored vault token, no restart needed).

import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0241_addon_cli_config_alter_addon_addon_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='infisical_service_token',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', max_length=512),
        ),
    ]
