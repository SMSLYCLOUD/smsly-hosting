# Add TLS verification controls to ManagedServer.
#
# Audit finding (Batch G, item 3.5): the platform was calling
# remote nodes with ``verify=False`` on every HTTP request,
# which allowed a network-adjacent attacker to MITM the
# connection and capture the gateway_secret / SSH password sent
# to the remote. The fix is to make TLS verification the default
# and require an explicit per-server ``verify_tls=False`` flag
# (gated by the ALLOW_INSECURE_INTER_NODE_TLS env flag) plus an
# optional SHA-256 cert pin.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0091_platformconfig_cloudflare_token_max_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='managedserver',
            name='verify_tls',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'If false, the platform skips TLS verification when '
                    "calling this server's API. Requires the "
                    'ALLOW_INSECURE_INTER_NODE_TLS env flag.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='managedserver',
            name='tls_cert_sha256',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    'Optional SHA-256 fingerprint of the server TLS cert '
                    '(hex, no colons). When set, connections are pinned '
                    'to this cert regardless of the system trust store.'
                ),
                max_length=64,
            ),
        ),
    ]
