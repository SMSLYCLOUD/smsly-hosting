from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0253_platformconfig_openappsec_mode_prevent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="service",
            name="use_internal_network",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Opt in to dual-homing on the platform-wide 'smsly-platform-net' "
                    "bridge for cross-project internal traffic. Disabled by default so "
                    "services stay isolated to their project's scoped bridge."
                ),
            ),
        ),
    ]
