from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0161_alter_service_autoscale_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='auto_review_hours',
            field=models.PositiveIntegerField(
                default=2,
                help_text='Auto-approve deployments in REVIEW status after this many hours (0 = disabled)',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='auto_promote_hours',
            field=models.PositiveIntegerField(
                default=12,
                help_text='Auto-promote deployments in STAGED status after this many hours (0 = disabled)',
            ),
        ),
    ]
