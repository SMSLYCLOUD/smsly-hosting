from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0181_service_path_redirects'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='host_aliases',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Extra hostnames that serve THIS service directly. '
                    'Each entry is {"host": "account.example.com", "rewrite_root": "/login"}. '
                    'Visiting the alias serves the app; the root path is rewritten to '
                    'rewrite_root (e.g. /login) so account.example.com shows the login '
                    'page. Other paths pass through unchanged.'
                ),
            ),
        ),
    ]
