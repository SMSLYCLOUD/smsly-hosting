# Add source SSH credentials for node-to-node transfers

import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0078_s3_backup_storage'),
    ]

    operations = [
        migrations.AddField(
            model_name='servertransfer',
            name='source_server_id',
            field=models.CharField(blank=True, default='', help_text='ManagedServer UUID when source is a known node', max_length=255),
        ),
        migrations.AddField(
            model_name='servertransfer',
            name='source_ssh_key',
            field=encrypted_model_fields.fields.EncryptedTextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='servertransfer',
            name='source_ssh_password',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
    ]
