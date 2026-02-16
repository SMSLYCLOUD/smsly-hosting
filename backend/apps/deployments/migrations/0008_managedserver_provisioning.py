# Generated migration for auto-provisioning fields

from django.db import migrations, models
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0007_platformconfig"),
    ]

    operations = [
        # Make api_url and api_token optional (filled by provisioner)
        migrations.AlterField(
            model_name="managedserver",
            name="api_url",
            field=models.URLField(
                blank=True, default="",
                help_text="Full URL to the SMSLY Hosting API (auto-filled after provisioning)",
            ),
        ),
        migrations.AlterField(
            model_name="managedserver",
            name="api_token",
            field=encrypted_model_fields.fields.EncryptedCharField(
                max_length=255, blank=True, default="",
                help_text="Bearer token for the remote API (auto-filled after provisioning)",
            ),
        ),
        # SSH credentials
        migrations.AddField(
            model_name="managedserver",
            name="ssh_user",
            field=models.CharField(
                default="root", max_length=100,
                help_text="SSH username (usually root)",
            ),
        ),
        migrations.AddField(
            model_name="managedserver",
            name="ssh_password",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, default="", max_length=255,
                help_text="SSH password (encrypted at rest)",
            ),
        ),
        migrations.AddField(
            model_name="managedserver",
            name="ssh_key",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True, default="",
                help_text="SSH private key content (encrypted at rest)",
            ),
        ),
        # Provisioning state
        migrations.AddField(
            model_name="managedserver",
            name="provision_status",
            field=models.CharField(
                choices=[
                    ("NONE", "Not provisioned"),
                    ("PENDING", "Pending"),
                    ("PROVISIONING", "Provisioning"),
                    ("DONE", "Done"),
                    ("FAILED", "Failed"),
                ],
                default="NONE", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="managedserver",
            name="provision_logs",
            field=models.TextField(blank=True, default=""),
        ),
    ]
