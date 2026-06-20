"""
Migration for tunnel model enhancements and subdomain reservations.

Adds:
  - Tunnel.type (http/tcp)
  - Tunnel.shared_with (JSON list of emails)
  - Tunnel.bandwidth_bytes (total bandwidth)
  - Tunnel.expires_at (optional expiration)
  - ReservedSubdomain model
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('deployments', '0028_alter_service_is_public'),
    ]

    operations = [
        # ── Tunnel field additions ────────────────────────────────────
        migrations.AddField(
            model_name='tunnel',
            name='type',
            field=models.CharField(
                choices=[('http', 'HTTP'), ('tcp', 'TCP')],
                default='http',
                max_length=4,
            ),
        ),
        migrations.AddField(
            model_name='tunnel',
            name='shared_with',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='List of email addresses this tunnel is shared with',
            ),
        ),
        migrations.AddField(
            model_name='tunnel',
            name='bandwidth_bytes',
            field=models.BigIntegerField(
                default=0,
                help_text='Total bandwidth used in bytes',
            ),
        ),
        migrations.AddField(
            model_name='tunnel',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # ── ReservedSubdomain model ──────────────────────────────────
        migrations.CreateModel(
            name='ReservedSubdomain',
            fields=[
                ('id', models.UUIDField(
                    default=uuid.uuid4,
                    editable=False,
                    primary_key=True,
                    serialize=False)),
                ('subdomain', models.CharField(max_length=63, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reserved_subdomains',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
