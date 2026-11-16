from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0251_platformconfig_node_local_build_default_and_more'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='managedserver',
            constraint=models.UniqueConstraint(
                fields=['node_number'],
                name='ms_node_number_unique',
                condition=models.Q(node_number__isnull=False),
            ),
        ),
    ]
