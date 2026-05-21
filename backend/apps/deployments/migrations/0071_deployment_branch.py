from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0070_auditlog_database_triggers'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='branch',
            field=models.CharField(
                blank=True, default='', max_length=255,
                help_text='Branch name this deployment deploys (overrides service default)'),
        ),
    ]
