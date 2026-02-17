# Generated manually for security audit H-1 fix
import encrypted_model_fields.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0016_alter_environmentvariable_value'),
    ]

    operations = [
        migrations.AlterField(
            model_name='addon',
            name='connection_url',
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, max_length=512),
        ),
    ]
