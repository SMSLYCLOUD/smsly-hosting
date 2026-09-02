# Generated migration: preview environment access control.

# Preview environments (PR deployments, often carrying full DB clones
# of production data) were served at their public preview URLs with
# ZERO authentication — anyone who guessed or scraped the hostname
# (pr-42-myservice.grid.smsly.cloud) could browse the preview and its
# cloned database. Railway/Vercel both gate previews; this migration
# adds the platform toggle and per-service preview password used by
# the Caddyfile generator to emit a basic_auth directive on every
# preview hostname.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0193_edge_shield'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='preview_auth_required',
            field=models.BooleanField(
                default=True,
                help_text='Require basic-auth on all preview environment '
                          'URLs. Previews often carry cloned production '
                          'databases and must not be publicly browsable.'),
        ),
        migrations.AddField(
            model_name='service',
            name='preview_password',
            field=models.CharField(
                max_length=64, blank=True, default='',
                help_text='Basic-auth password for this service\'s preview '
                          'environments. Auto-generated on first preview '
                          'deploy when empty; username is always "preview".'),
        ),
    ]
