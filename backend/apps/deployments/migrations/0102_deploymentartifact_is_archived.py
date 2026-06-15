from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0101_previewenvironment_unique_service_branch_commit'),
    ]

    operations = [
        migrations.AddField(
            model_name='deploymentartifact',
            name='is_archived',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Soft-delete flag: when True the row is hidden from '
                    'default querysets but is preserved for audit.'
                ),
            ),
        ),
    ]
