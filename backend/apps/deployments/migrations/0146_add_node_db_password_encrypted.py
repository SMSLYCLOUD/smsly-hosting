import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0145_add_local_role_to_cluster_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedserver",
            name="node_db_password",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "Dedicated PostgreSQL password for this node's agent. "
                    "Encrypted at rest via FIELD_ENCRYPTION_KEY."
                ),
                max_length=255,
            ),
        ),
    ]
