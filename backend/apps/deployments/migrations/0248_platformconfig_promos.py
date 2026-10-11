from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0247_service_addon_placement'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='promos_enabled',
            field=models.BooleanField(default=False, help_text='Master kill-switch for the dashboard Trulay cross-sell banner. Off by default (OSS self-hosts stay ad-free).'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='promos_rotate_seconds',
            field=models.PositiveIntegerField(default=8, help_text='Seconds each promo card shows before rotating (5-60).'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='promos_items',
            field=models.JSONField(blank=True, default=list, help_text='Cross-sell cards: list of {id, title, description, href, cta, color, bg}. Empty falls back to the built-in Trulay defaults in the frontend.'),
        ),
    ]
