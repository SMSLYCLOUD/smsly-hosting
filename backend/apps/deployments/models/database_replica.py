"""
Database read-replica models.

A DatabaseReplica represents a PostgreSQL read-only endpoint that
pgcat can route SELECTs to. It can be:

  * A local docker container brought up via docker-compose.replica.yml
    (kind='local', typically 'db-replica:5432' on the smsly-net bridge).
  * A remote self-hosted PostgreSQL with streaming replication
    configured (e.g. a second VPS running a hot-standby).
  * A managed read-replica endpoint from a cloud provider
    (AWS RDS, DigitalOcean, Crunchy Bridge, Supabase, Neon, etc.).

The backend's pgcat config (render_pgcat_config.py) reads the
active replica list and adds them to the shards so SELECTs can be
split off the primary. Writes always go to the primary regardless.

Replicas are admin-only managed: only superusers can create, edit,
or delete them. The encryption of the password field is mandatory
because the password is needed at runtime by the pgcat container
for the connection pool — it is decrypted only at config-render
time, never returned in API responses.
"""

import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class DatabaseReplica(models.Model):
    """
    A PostgreSQL read-replica endpoint that pgcat can route SELECTs to.
    """

    class Kind(models.TextChoices):
        LOCAL = "local", "Local (docker container on the master host)"
        REMOTE = "remote", "Remote (separate host or managed DB)"

    class SslMode(models.TextChoices):
        DISABLE = "disable", "disable (plaintext, LAN only)"
        ALLOW = "allow", "allow (prefer TLS, fall back to plaintext)"
        PREFER = "prefer", "prefer (try TLS first, fall back to plaintext)"
        REQUIRE = "require", "require (TLS, do not verify cert)"
        VERIFY_CA = "verify-ca", "verify-ca (TLS + verify CA)"
        VERIFY_FULL = "verify-full", "verify-full (TLS + verify CA + hostname)"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown (not yet tested)"
        OK = "ok", "OK (reachable, accepting connections)"
        WARN = "warn", "Warning (reachable but lag > threshold)"
        ERROR = "error", "Error (not reachable or auth failed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]

    name = models.CharField(  # type: ignore[var-annotated]
        max_length=120,
        unique=True,
        help_text="Human-readable label, e.g. 'europe-rds', 'remote-standby'",
    )

    kind = models.CharField(  # type: ignore[var-annotated]
        max_length=16,
        choices=Kind.choices,
        default=Kind.REMOTE,
        help_text=(
            "Local = docker container on the master host. "
            "Remote = separate host or managed DB endpoint."
        ),
    )

    host = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        help_text="Hostname or IP address (no scheme, no port). For local kind this is the docker service name e.g. 'db-replica'.",
    )
    port = models.PositiveIntegerField(  # type: ignore[var-annotated]
        default=5432,
        help_text="PostgreSQL port (default 5432).",
    )
    database = models.CharField(  # type: ignore[var-annotated]
        max_length=120,
        default="smsly_hosting",
        help_text="PostgreSQL database name.",
    )
    username = models.CharField(  # type: ignore[var-annotated]
        max_length=120,
        help_text="PostgreSQL role to connect as. For streaming replicas this is the user the primary created via CREATE ROLE ... REPLICATION.",
    )
    password = EncryptedCharField(  # type: ignore[var-annotated]
        max_length=512,
        help_text="PostgreSQL password. Encrypted at rest using FIELD_ENCRYPTION_KEY. Returned as the empty string in API responses — use the dedicated update endpoint to rotate.",
    )

    ssl_mode = models.CharField(  # type: ignore[var-annotated]
        max_length=16,
        choices=SslMode.choices,
        default=SslMode.PREFER,
        help_text="SSL/TLS mode. Use 'require' or stronger for any replica reachable over the public internet.",
    )
    ssl_ca_path = models.CharField(  # type: ignore[var-annotated]
        max_length=512,
        blank=True,
        default="",
        help_text="Optional: path to a CA bundle (mounted into the pgcat container) used for verify-ca / verify-full modes.",
    )

    is_active = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="When unchecked, the replica is excluded from pgcat config but the row is preserved for history.",
    )

    # Health / monitoring — written by the periodic health-check task.
    last_status = models.CharField(  # type: ignore[var-annotated]
        max_length=16,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    last_error = models.TextField(  # type: ignore[var-annotated]
        blank=True,
        default="",
        help_text="Last error message from the health check (cleared on next successful check).",
    )
    lag_seconds = models.FloatField(  # type: ignore[var-annotated]
        null=True,
        blank=True,
        help_text="Replication lag in seconds (read from pg_stat_replication on the primary). Null when the replica is not currently being streamed.",
    )

    # Streaming replication state (for local and remote replicas that
    # are streamed from this primary). Populated by the health-check
    # task via SELECT * FROM pg_stat_replication WHERE application_name = name.
    application_name = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        default="",
        help_text="Application name to identify this replica in pg_stat_replication. Defaults to the row's name field. Only relevant for replicas that use streaming replication from this primary.",
    )

    notes = models.TextField(  # type: ignore[var-annotated]
        blank=True,
        default="",
        help_text="Free-form operator notes (provider, region, cost, etc.).",
    )

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]
    created_by = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_database_replicas",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Database Replica"
        verbose_name_plural = "Database Replicas"
        indexes = [
            models.Index(fields=["is_active", "kind"]),
            models.Index(fields=["last_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port}/{self.database})"

    @property
    def pgcat_endpoint(self) -> str:
        """Return the ``host:port`` form used in pgcat shards.0.servers."""
        return f"{self.host}:{self.port}"

    def sslmode_for_libpq(self) -> str:
        """
        Map the user-friendly SslMode value to the libpq ``sslmode`` string
        that pgcat uses. The values are mostly identical; this method
        exists so that future divergence (e.g. channel binding) is a
        single place to change.
        """
        return {
            self.SslMode.DISABLE: "disable",
            self.SslMode.ALLOW: "allow",
            self.SslMode.PREFER: "prefer",
            self.SslMode.REQUIRE: "require",
            self.SslMode.VERIFY_CA: "verify-ca",
            self.SslMode.VERIFY_FULL: "verify-full",
        }.get(self.ssl_mode, "prefer")
