from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0182_service_host_aliases'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='wildcard_internal_only',
            field=models.BooleanField(
                blank=True,
                default=False,
                help_text=(
                    'When enabled, the auto-generated wildcard domain is hidden from '
                    'the public internet (visitors get the 503 page) but still routes '
                    'for internal/mesh traffic. Custom domains keep working normally.'
                ),
                null=True,
            ),
        ),
    ]
