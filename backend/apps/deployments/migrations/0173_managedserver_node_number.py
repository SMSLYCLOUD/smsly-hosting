from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0172_service_url_toggles_nullable'),
    ]

    operations = [
        migrations.AddField(
            model_name='managedserver',
            name='node_number',
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text='Sequential node number (1, 2, ...). Used for domain naming: grid{N}.domain.',
            ),
        ),
    ]
