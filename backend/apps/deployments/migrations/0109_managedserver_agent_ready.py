"""
Add agent self-registration fields to ManagedServer.

The agent-side registrar (a small service running inside the lite
agent docker-compose stack) needs a way to tell the master "I'm
booted, fully provisioned, and reachable on these endpoints".

Two new fields on ManagedServer:

- ``agent_ready``: True once the agent has finished installing and
  reports success. Distinct from ``status=ONLINE`` which only
  indicates the master can reach the API; ``agent_ready`` is the
  agent's own assertion that the install completed end-to-end
  (containers up, migrations applied, celery worker subscribed).

- ``last_agent_heartbeat_at``: timestamp of the most recent
  registrar heartbeat. The master flips ``status`` to OFFLINE
  if no heartbeat has been received for >120s. Decoupled from
  ``last_health_check`` (which is the master's outbound probe)
  so an inbound-vs-outbound outage is diagnosable.

- ``agent_runtime_info``: JSON snapshot of the agent's local
  state on its last heartbeat (docker version, smsly image
  versions, host uptime, disk/mem). Stored as JSONField to keep
  the schema flexible — agents can add fields without
  migrations.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0108_database_replica'),
    ]

    operations = [
        migrations.AddField(
            model_name='managedserver',
            name='agent_ready',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True once the agent's installer/registrar has reported "
                    "it has finished bootstrapping and is fully ready to "
                    "accept work. Distinct from status=ONLINE which only "
                    "indicates the master can reach the API."
                ),
            ),
        ),
        migrations.AddField(
            model_name='managedserver',
            name='last_agent_heartbeat_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Last time the agent's registrar sent a heartbeat. "
                    "Used to detect silent agent outages even when the "
                    "API is unreachable from the master."
                ),
            ),
        ),
        migrations.AddField(
            model_name='managedserver',
            name='agent_runtime_info',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Last-seen runtime snapshot from the agent: docker "
                    "version, image versions, host uptime, disk/mem. "
                    "Refreshed on every heartbeat."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name='managedserver',
            index=models.Index(
                fields=['is_lite_agent', 'agent_ready'],
                name='deployments_lite_ready_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='managedserver',
            index=models.Index(
                fields=['last_agent_heartbeat_at'],
                name='deployments_last_ag_idx',
            ),
        ),
    ]
