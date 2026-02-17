# Generated migration for EnvironmentVariable.value blank=True

import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0015_service_function_code_service_function_runtime'),
    ]

    operations = [
        migrations.AlterField(
            model_name='environmentvariable',
            name='value',
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, default='', max_length=255),
        ),
    ]
