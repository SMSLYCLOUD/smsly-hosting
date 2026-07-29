"""
Database replica management API.

Endpoints (all admin-only):
  * GET    /database-replicas/             list
  * POST   /database-replicas/             create
  * GET    /database-replicas/{id}/        detail
  * PATCH  /database-replicas/{id}/        update
  * DELETE /database-replicas/{id}/        remove
  * POST   /database-replicas/{id}/test/   test connection (no save)
  * POST   /database-replicas/sync/        push config to pgcat
  * GET    /database-replicas/endpoints/   comma-separated host:port list

The password field is write-only: it is never returned in any
response (including detail). To rotate a password, PATCH the row
with the new value.
"""

import logging

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from ..models.database_replica import DatabaseReplica
from ..services import database_replica_service as svc

logger = logging.getLogger(__name__)


# ─── Serializers ───────────────────────────────────────────────────────────────


class DatabaseReplicaSerializer(serializers.ModelSerializer):
    """Default serializer. The password field is never returned."""

    # EncryptedCharField isn't auto-mapped by DRF's ModelSerializer,
    # so declare the field explicitly. write_only=True keeps the
    # plaintext password out of every response (including detail);
    # callers must PATCH the field to rotate it.
    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        style={"input_type": "password"},
        help_text=(
            "PostgreSQL password. Encrypted at rest using "
            "FIELD_ENCRYPTION_KEY. Never returned in responses."
        ),
    )

    class Meta:
        model = DatabaseReplica
        fields = [
            "id",
            "name",
            "kind",
            "host",
            "port",
            "database",
            "username",
            "password",
            "ssl_mode",
            "ssl_ca_path",
            "is_active",
            "last_status",
            "last_checked_at",
            "last_error",
            "lag_seconds",
            "application_name",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "last_status",
            "last_checked_at",
            "last_error",
            "lag_seconds",
            "created_at",
            "updated_at",
        ]

    def validate_port(self, value: int) -> int:
        if not (1 <= value <= 65535):
            raise serializers.ValidationError("Port must be between 1 and 65535.")
        return value

    def validate_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name is required.")
        return value

    def validate(self, attrs):
        # On create, password is required (we never want an unusable
        # replica in the config). On update, leave it alone if the
        # caller didn't include it.
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError(
                {"password": "Password is required when creating a replica."}
            )
        return attrs


class DatabaseReplicaCreateSerializer(DatabaseReplicaSerializer):
    """Used on POST. Requires the password (write-only)."""

    # Override to make password required on create.
    password = serializers.CharField(
        write_only=True,
        required=True,
        allow_blank=False,
        style={"input_type": "password"},
    )


# ─── ViewSet ──────────────────────────────────────────────────────────────────


class DatabaseReplicaViewSet(viewsets.ModelViewSet):
    """
    Manage read-replica endpoints that pgcat can route SELECTs to.

    Permissions: IsAdminUser (only superusers).
    """

    permission_classes = [IsAdminUser]
    queryset = DatabaseReplica.objects.all().order_by("name")
    lookup_field = "id"

    def get_serializer_class(self):
        if self.action == "create":
            return DatabaseReplicaCreateSerializer
        return DatabaseReplicaSerializer

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        # Don't auto-sync on create: the operator may add several
        # replicas in a row and trigger /sync/ once at the end.
        logger.info("DatabaseReplica %s created by %s", instance.name, self.request.user)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        name = instance.name
        instance.delete()
        logger.info("DatabaseReplica %s deleted", name)

    # ─── Custom actions ────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def test(self, request, id=None):
        """
        Test the TCP reachability of a configured replica.

        Does NOT save anything to the DB. To persist the result,
        the periodic health-check task or the operator can call
        ``/sync/`` to apply changes to pgcat.

        Returns:
            200 { "reachable": true|false, "error": "...", "lag_seconds": null }
        """
        replica = self.get_object()
        ok, err, lag = svc.test_connection(replica)
        return Response(
            {
                "reachable": ok,
                "error": err,
                "lag_seconds": lag,
                "endpoint": replica.pgcat_endpoint,
            }
        )

    @action(detail=False, methods=["post"])
    def sync(self, request):
        """
        Push the current set of active replicas to the pgcat
        container and reload it.

        Returns 200 with a summary, or 502 if the docker call
        failed (in which case the caller can decide whether to
        surface the error to the operator).
        """
        result = svc.sync_pgcat_config()
        if result.get("error"):
            return Response(result, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def endpoints(self, request):
        """
        Return the comma-separated ``host:port`` list as JSON.

        This mirrors the DB_REPLICA_HOSTS env var and is what
        pgcat's render_pgcat_config.py consumes.
        """
        return Response(
            {
                "endpoints": svc.replica_endpoints_for_pgcat(),
                "count": len(svc.active_replica_endpoints()),
            }
        )
