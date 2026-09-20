# Generated for openappsec UI toggle (Settings → Security Scanning).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0224_server_status_degraded'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='openappsec_enabled',
            field=models.BooleanField(default=True, help_text='Enable the open-appsec WAF shadow stack (agent + Envoy attachment, detect-learn). Toggled from Settings → Security Scanning; synced to OPENAPPSEC_ENABLED in .env for the installer reconcile.'),
        ),
        migrations.AlterField(
            model_name='managedserver',
            name='node_components',
            field=models.JSONField(blank=True, default=dict, help_text='Optional components enabled on this node. Keys: observability (cadvisor/node-exporter/docker-labels metrics), security (reserved — host/kernel hardening via fail2ban/ufw/apparmor/auditd/kernel/gvisor always runs on nodes regardless of this flag), crowdsec, falco, spire (spire-agent, spire-agent-ecosystem), log_shipping (ship node access logs to master CrowdSec for centralized edge analysis; independent of observability).'),
        ),
    ]
