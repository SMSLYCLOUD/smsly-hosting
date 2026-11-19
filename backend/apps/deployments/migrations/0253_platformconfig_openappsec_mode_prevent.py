from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0252_managedserver_node_number_unique'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='openappsec_enabled',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Enable the open-appsec WAF shadow stack (agent + Envoy '
                    'attachment, prevent by default). Toggled from Settings → '
                    'Security Scanning; synced to OPENAPPSEC_ENABLED in .env '
                    'for the installer reconcile.'
                ),
            ),
        ),
        migrations.AlterField(
            model_name='platformconfig',
            name='openappsec_mode',
            field=models.CharField(
                choices=[
                    ('detect-learn', 'Shadow — detect and learn'),
                    ('prevent', 'Enforce — block'),
                ],
                default='prevent',
                help_text=(
                    'WAF enforcement mode. Enforce blocks malicious requests '
                    'inline (default); shadow observes without blocking. Set from '
                    'Settings → Security Scanning; applied to local_policy.yaml '
                    'by the installer reconcile and periodic policy sync.'
                ),
                max_length=16,
            ),
        ),
    ]
