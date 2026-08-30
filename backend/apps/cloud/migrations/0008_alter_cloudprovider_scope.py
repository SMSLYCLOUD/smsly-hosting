from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0007_cloudprovider_scope'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudprovider',
            name='scope',
            field=models.CharField(
                blank=True,
                default='platform',
                help_text=(
                    "Tenant scope: 'platform' (default), 'ecosystem', or "
                    "custom. Used by the ecosystem deploy task to pick or "
                    "auto-create a provider that's dedicated to the "
                    "ecosystem so its services don't share Docker network "
                    "or registry with platform workloads."
                ),
                max_length=32,
            ),
        ),
    ]
