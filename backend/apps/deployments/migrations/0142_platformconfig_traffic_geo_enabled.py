from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0141_rename_deployments_service_country_code_idx_deployments_service_991d77_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='traffic_geo_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Collect Traefik access logs and resolve IP geolocations for the traffic map.',
            ),
        ),
    ]
