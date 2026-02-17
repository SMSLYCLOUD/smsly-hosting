# Generated migration for domains related_name fix
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0012_apitoken_and_more"),
        ("domains", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="domain",
            name="service",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="domain_instances",
                to="deployments.service",
            ),
        ),
    ]
