from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("deployments", "0052_alter_addon_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="source_node",
            field=models.CharField(
                max_length=255,
                blank=True,
                null=True,
                help_text="Node that triggered this deployment (for multi-deploy)",
            ),
        ),
    ]

