# Generated migration: configurable wildcard proxying (Edge Shield).

# The Edge Shield rollout proxied every record including wildcards
# (*.grid.smsly.cloud). Cloudflare Universal SSL covers zone +
# first-level wildcard only (smsly.cloud, *.smsly.cloud) — NOT
# third-level (*.grid.smsly.cloud). Proxied third-level wildcards made
# every deployed service handshake-fail (ERR_SSL_VERSION_OR_CIPHER_MISMATCH).
# This adds the settings-page toggle; default OFF keeps wildcards
# DNS-only so origin on-demand TLS serves them.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0194_preview_auth'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='edge_proxy_wildcards',
            field=models.BooleanField(
                default=False,
                help_text='Edge Shield: ALSO proxy wildcard records. '
                          'DANGEROUS for third-level wildcards — Cloudflare '
                          'Universal SSL does not cover them.'),
        ),
    ]
