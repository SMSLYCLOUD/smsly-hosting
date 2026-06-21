import django.db.models.deletion
import encrypted_model_fields.fields
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add the DatabaseReplica model.

    A DatabaseReplica represents a PostgreSQL read-only endpoint
    (local docker container, remote self-hosted standby, or a
    managed read-replica endpoint from a cloud provider) that pgcat
    can route SELECTs to.

    See models_database_replica.py for the field-level rationale.
    """

    dependencies = [
        ('deployments', '0107_alter_deployment_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DatabaseReplica',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text="Human-readable label, e.g. 'europe-rds', 'remote-standby'", max_length=120, unique=True)),
                ('kind', models.CharField(choices=[('local', 'Local (docker container on the master host)'), ('remote', 'Remote (separate host or managed DB)')], default='remote', help_text='Local = docker container on the master host. Remote = separate host or managed DB endpoint.', max_length=16)),
                ('host', models.CharField(help_text="Hostname or IP address (no scheme, no port). For local kind this is the docker service name e.g. 'db-replica'.", max_length=255)),
                ('port', models.PositiveIntegerField(default=5432, help_text='PostgreSQL port (default 5432).')),
                ('database', models.CharField(default='smsly_hosting', help_text='PostgreSQL database name.', max_length=120)),
                ('username', models.CharField(help_text='PostgreSQL role to connect as. For streaming replicas this is the user the primary created via CREATE ROLE ... REPLICATION.', max_length=120)),
                ('password', encrypted_model_fields.fields.EncryptedCharField(help_text='PostgreSQL password. Encrypted at rest using FIELD_ENCRYPTION_KEY. Returned as the empty string in API responses — use the dedicated update endpoint to rotate.', max_length=512)),
                ('ssl_mode', models.CharField(choices=[('disable', 'disable (plaintext, LAN only)'), ('allow', 'allow (prefer TLS, fall back to plaintext)'), ('prefer', 'prefer (try TLS first, fall back to plaintext)'), ('require', 'require (TLS, do not verify cert)'), ('verify-ca', 'verify-ca (TLS + verify CA)'), ('verify-full', 'verify-full (TLS + verify CA + hostname)')], default='prefer', help_text="SSL/TLS mode. Use 'require' or stronger for any replica reachable over the public internet.", max_length=16)),
                ('ssl_ca_path', models.CharField(blank=True, default='', help_text='Optional: path to a CA bundle (mounted into the pgcat container) used for verify-ca / verify-full modes.', max_length=512)),
                ('is_active', models.BooleanField(default=True, help_text='When unchecked, the replica is excluded from pgcat config but the row is preserved for history.')),
                ('last_status', models.CharField(choices=[('unknown', 'Unknown (not yet tested)'), ('ok', 'OK (reachable, accepting connections)'), ('warn', 'Warning (reachable but lag > threshold)'), ('error', 'Error (not reachable or auth failed)')], default='unknown', max_length=16)),
                ('last_checked_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='', help_text='Last error message from the health check (cleared on next successful check).')),
                ('lag_seconds', models.FloatField(blank=True, help_text='Replication lag in seconds (read from pg_stat_replication on the primary). Null when the replica is not currently being streamed.', null=True)),
                ('application_name', models.CharField(blank=True, default='', help_text="Application name to identify this replica in pg_stat_replication. Defaults to the row's name field. Only relevant for replicas that use streaming replication from this primary.", max_length=255)),
                ('notes', models.TextField(blank=True, default='', help_text='Free-form operator notes (provider, region, cost, etc.).')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_database_replicas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Database Replica',
                'verbose_name_plural': 'Database Replicas',
                'ordering': ['name'],
                'indexes': [
                    models.Index(fields=['is_active', 'kind'], name='deployments_is_acti_d1b4be_idx'),
                    models.Index(fields=['last_status'], name='deployments_last_st_5a2d75_idx'),
                ],
            },
        ),
    ]
