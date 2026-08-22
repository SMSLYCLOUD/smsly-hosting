from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0180_service_wildcard_redirect_custom_domain'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='domain_path_redirects',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Path-to-subdomain redirects on the primary domain. '
                    'Each entry is {"path": "/account", "target": "account.example.com"}. '
                    'Requests to /account/* are 301-redirected to https://target/... '
                    '(prefix stripped, query preserved). Reserved platform paths '
                    '(/api, /admin, ...) are rejected.'
                ),
            ),
        ),
    ]
