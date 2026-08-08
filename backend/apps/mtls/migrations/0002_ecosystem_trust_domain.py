from django.db import migrations, models


def migrate_trust_domain(apps, schema_editor):
    """Update existing configs that used the old default trust domain."""
    MtlsConfig = apps.get_model("mtls", "MtlsConfig")
    MtlsConfig.objects.filter(trust_domain="platform.local").update(
        trust_domain="ecosystem.local"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("mtls", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mtlsconfig",
            name="trust_domain",
            field=models.CharField(
                default="ecosystem.local",
                help_text="SPIFFE trust domain for this service (ecosystem.local for user services).",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="mtlsconfig",
            name="spiffe_id",
            field=models.CharField(
                blank=True,
                help_text="Auto-generated SPIFFE ID (e.g., spiffe://ecosystem.local/service/my-app).",
                max_length=512,
            ),
        ),
        migrations.RunPython(migrate_trust_domain, migrations.RunPython.noop),
    ]
