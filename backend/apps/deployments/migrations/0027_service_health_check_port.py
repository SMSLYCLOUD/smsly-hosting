from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0026_service_deploy_mode_compose_is_public'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='health_check_port',
            field=models.IntegerField(
                blank=True,
                help_text='Port for health checks. Leave blank to auto-detect from PORT env var.',
                null=True,
            ),
        ),
    ]
