from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import encrypted_model_fields.fields


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("deployments", "0053_deployment_source_node"),
    ]

    operations = [
        migrations.AddField(
            model_name="servertransfer",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="server_transfers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="servertransfer",
            name="target_ssh_key",
            field=encrypted_model_fields.fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="servertransfer",
            name="target_ssh_password",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default="",
                max_length=255,
            ),
        ),
    ]

