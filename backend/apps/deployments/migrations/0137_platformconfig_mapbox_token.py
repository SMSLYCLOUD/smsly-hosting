from django.db import migrations, models
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0136_service_disable_crowdsec_waf'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='mapbox_token',
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                default='',
                help_text=(
                    'Mapbox GL token for the traffic world map on the Metrics page. '
                    'Falls back to NEXT_PUBLIC_MAPBOX_TOKEN env var if empty.'
                ),
                max_length=512,
            ),
        ),
    ]
