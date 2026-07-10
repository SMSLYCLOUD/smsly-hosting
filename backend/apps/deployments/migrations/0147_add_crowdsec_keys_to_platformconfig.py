import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0146_add_node_db_password_encrypted"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfig",
            name="crowdsec_bouncer_key",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "CrowdSec bouncer API key for Traefik authentication. "
                    "Falls back to CROWDSEC_BOUNCER_KEY env var if empty."
                ),
                max_length=256,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="crowdsec_enroll_key",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "CrowdSec console enrollment key (optional). "
                    "Falls back to CROWDSEC_ENROLL_KEY env var if empty."
                ),
                max_length=256,
            ),
        ),
    ]
