from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0099_addon_service_public_domain_unique'),
    ]

    operations = [
        migrations.AlterField(
            model_name='volume',
            name='size_gb',
            field=models.IntegerField(
                default=1,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(1000),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name='volume',
            constraint=models.CheckConstraint(
                check=models.Q(size_gb__gte=1) & models.Q(size_gb__lte=1000),
                name='volume_size_gb_range',
            ),
        ),
    ]
