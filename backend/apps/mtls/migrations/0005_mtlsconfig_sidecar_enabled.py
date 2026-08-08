from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mtls", "0004_mtlsauthorizationpolicy"),
    ]

    operations = [
        migrations.AddField(
            model_name="mtlsconfig",
            name="sidecar_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Enable Envoy sidecar for transparent mTLS. "
                    "When enabled, an Envoy proxy is deployed alongside the service "
                    "to handle mTLS termination/origination transparently."
                ),
            ),
        ),
    ]
