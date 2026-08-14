# Generated for min_replicas scale-to-zero support.

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0164_add_node_scorer_config_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='min_replicas',
            field=models.IntegerField(default=1, validators=[MinValueValidator(0)]),
        ),
    ]
