from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0096_migrationvalidation_auto_deploy_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='reservedsubdomain',
            name='released_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='reservedsubdomain',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='reservedsubdomain',
            name='subdomain',
            field=models.CharField(max_length=63),
        ),
        migrations.AddConstraint(
            model_name='reservedsubdomain',
            constraint=models.UniqueConstraint(
                fields=['subdomain'],
                condition=Q(is_active=True),
                name='unique_active_reservedsubdomain_subdomain',
            ),
        ),
    ]
