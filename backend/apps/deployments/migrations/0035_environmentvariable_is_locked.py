# Generated migration: Add is_locked to EnvironmentVariable,
# add SYSTEM source choice.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0034_serverbackup_error_message_addon_type_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name='environmentvariable',
            name='is_locked',
            field=models.BooleanField(
                default=False,
                help_text='Locked vars are never overridden by platform auto-injection during deployment',
            ),
        ),
        migrations.AlterField(
            model_name='environmentvariable',
            name='source',
            field=models.CharField(
                choices=[
                    ('USER', 'User Defined'),
                    ('ADDON', 'Addon Auto-Injected'),
                    ('SHORTCODE', 'Shortcode Resolved'),
                    ('SYSTEM', 'System Auto-Injected'),
                ],
                default='USER',
                help_text='Origin of this env var',
                max_length=20,
            ),
        ),
    ]
