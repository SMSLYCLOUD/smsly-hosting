# Generated for project-scoped MCP/CLI API tokens.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_apitoken_scopes'),
    ]

    operations = [
        migrations.AddField(
            model_name='apitoken',
            name='projects',
            field=models.JSONField(blank=True, default=list, help_text="Project IDs this token may access. Empty = all owner's projects (legacy)."),
        ),
    ]
