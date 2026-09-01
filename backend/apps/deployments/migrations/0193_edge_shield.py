# Generated migration: Edge Shield — BGP-hijack protection controls.
#
# The platform's DNS records resolve directly to the OVH origin IP
# (verified live: grid.smsly.cloud -> 176.31.201.181, AS16276, DNS-only
# on Cloudflare). A BGP hijack of the covering prefix redirects user
# traffic to an attacker with zero platform-side detection or defense.
#
# The Edge Shield fields drive the defense-in-depth stack:
#   edge_proxy_records   — Cloudflare-proxied records (Anycast + DDoS
#                          + BGP-hijack independence). When enabled,
#                          80/443 traffic flows through Cloudflare.
#   edge_origin_lockdown — iptables 80/443 accept ONLY Cloudflare IP
#                          ranges; direct-to-origin bypass of the edge
#                          becomes impossible. SSH unaffected.
#   edge_dnssec          — enable DNSSEC on the Cloudflare zone and
#                          surface the DS record for the registrar.
#   edge_shield_enabled  — master toggle written by deploy_edge_shield.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0192_deployment_runtime_logs'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='edge_proxy_records',
            field=models.BooleanField(
                default=False,
                help_text='Edge Shield: route DNS records through the '
                          'Cloudflare proxy (Anycast) instead of DNS-only '
                          'to the origin IP. Absorbs BGP hijack of the '
                          'origin prefix and L3-L4 DDoS.'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='edge_origin_lockdown',
            field=models.BooleanField(
                default=False,
                help_text='Edge Shield: firewall 80/443 on the host to '
                          'accept only Cloudflare IP ranges so hijacked '
                          'or direct traffic cannot bypass the edge. '
                          'SSH and the OVH/WireGuard mesh are unaffected.'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='edge_dnssec',
            field=models.BooleanField(
                default=False,
                help_text='Edge Shield: enable DNSSEC on the Cloudflare '
                          'zone and surface the DS record for the '
                          'registrar (blocks DNS-response forgery, the '
                          'other half of a BGP hijack).'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='edge_shield_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Edge Shield master toggle — True once the '
                          'deploy_edge_shield command has applied proxy '
                          '+ lockdown + DNSSEC successfully.'),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='edge_shield_ds_record',
            field=models.TextField(
                blank=True, default='',
                help_text='DS record (key, algo, digest) returned when '
                          'DNSSEC was enabled — must be added at the '
                          'registrar to complete the chain of trust.'),
        ),
    ]
