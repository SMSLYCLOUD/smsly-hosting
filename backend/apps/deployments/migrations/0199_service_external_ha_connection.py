from django.db import migrations, models
import encrypted_model_fields.fields


class Migration(migrations.Migration):
    dependencies = [
        ('deployments', '0197_service_ha_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='external_ha_endpoint',
            field=models.URLField(blank=True, default='', help_text='External HA control/data endpoint (HTTPS recommended).'),
        ),
        migrations.AddField(
            model_name='service',
            name='external_ha_username',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='service',
            name='external_ha_password',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', help_text='External HA credential encrypted at rest.', max_length=512),
        ),
        migrations.AddField(
            model_name='service',
            name='external_ha_database',
            field=models.CharField(blank=True, default='', help_text='Optional external PostgreSQL database name.', max_length=120),
        ),
    ]
