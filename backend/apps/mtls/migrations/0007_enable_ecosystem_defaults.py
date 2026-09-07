from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mtls", "0006_alter_mtlsconfig_trust_domain"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mtlsconfig",
            name="trust_domain",
            field=models.CharField(
                default="ecosystem.local",
                help_text="SPIFFE trust domain for this service.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="mtlsconfig",
            name="sidecar_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
