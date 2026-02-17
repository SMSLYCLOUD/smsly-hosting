# Generated migration for domains related_name fix
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0001_initial'),
        ('domains', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='domain',
            name='service',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='domain_set',
                to='deployments.service',
            ),
        ),
    ]
