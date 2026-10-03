from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('deployments', '0241_addon_cli_config_alter_addon_addon_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='edge_jwt_required',
            field=models.BooleanField(default=False, help_text='Require edge JWT at Traefik/Caddy before routing to this service'),
        ),
        migrations.AddField(
            model_name='service',
            name='sablier_enabled',
            field=models.BooleanField(default=False, help_text='Scale this service to zero when idle; wake on first request via Sablier'),
        ),
        migrations.AddField(
            model_name='service',
            name='sablier_session',
            field=models.CharField(default='10m', help_text='Sablier session duration before idle shutdown (e.g. 10m, 1h)', max_length=16),
        ),
        migrations.AddField(
            model_name='service',
            name='waf_opt_out',
            field=models.BooleanField(default=False, help_text='Opt this service out of Coraza WAF protection'),
        ),
    ]
