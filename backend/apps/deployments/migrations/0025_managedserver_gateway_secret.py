"""Add gateway_secret field to ManagedServer for HMAC V2 auth fallback."""

import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0024_project_service_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedserver",
            name="gateway_secret",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text="GATEWAY_SECRET of the remote server (for HMAC V2 auth fallback)",
                max_length=255,
            ),
        ),
    ]
