# Widen cloudflare_api_token from 255 to 512 chars to match Cloudflare's
# current token format and ensure the field stays EncryptedCharField
# (the previous schema also used EncryptedCharField; this migration
# documents the change in length and re-asserts the encrypted type).

import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0090_service_alert_config'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='cloudflare_api_token',
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default='',
                help_text='Cloudflare API Token for DNS challenge (Edit zone DNS)',
                max_length=512,
            ),
        ),
    ]
