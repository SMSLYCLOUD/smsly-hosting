from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0173_managedserver_node_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='managedserver',
            name='node_domain',
            field=models.CharField(
                max_length=255, blank=True, null=True,
                help_text='Computed node domain (e.g. grid1.smsly.cloud). Set during provisioning.',
            ),
        ),
    ]
