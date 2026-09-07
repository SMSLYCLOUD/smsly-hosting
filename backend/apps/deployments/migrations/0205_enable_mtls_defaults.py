from django.db import migrations, models


def enable_mtls_defaults(apps, schema_editor):
    PlatformConfig = apps.get_model("deployments", "PlatformConfig")
    PlatformConfig.objects.all().update(
        mtls_enabled=True,
        mtls_ecosystem_enabled=True,
    )
    MtlsConfig = apps.get_model("mtls", "MtlsConfig")
    # Preserve the trust domain: platform services must remain on the
    # platform SPIRE server; ecosystem services are normalized at deployment.
    MtlsConfig.objects.all().update(
        enabled=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("deployments", "0204_uplift_service_resources"),
        ("mtls", "0007_enable_ecosystem_defaults"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformconfig",
            name="mtls_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="platformconfig",
            name="mtls_ecosystem_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_mtls_defaults, migrations.RunPython.noop),
    ]
