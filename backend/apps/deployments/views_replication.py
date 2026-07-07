"""
Replication management views.

API endpoints for deploying, monitoring, and managing
Patroni-based PostgreSQL streaming replication.
"""

import ipaddress
import logging

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models_mesh import MeshNetwork
from .services.replication_service import ReplicationService

logger = logging.getLogger(__name__)


def _get_local_replica_health():
    """Return health status of postgres-replica containers (local HA stack)."""
    from .models_database_replica import DatabaseReplica
    from .services import database_replica_service as svc

    replicas = DatabaseReplica.objects.filter(is_active=True, kind="local")
    results = []
    for r in replicas:
        try:
            ok, err, lag = svc.test_connection(r)
            results.append({
                "name": r.name,
                "host": r.host,
                "port": r.port,
                "status": "OK" if ok else f"ERROR: {err}",
                "lag_seconds": lag,
                "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
            })
        except Exception as exc:
            results.append({
                "name": r.name,
                "host": r.host,
                "port": r.port,
                "status": f"ERROR: {exc}",
                "lag_seconds": None,
                "last_checked_at": None,
            })
    return results


# ─── Serializers ─────────────────────────────────────────────────────────────

class ReplicationDeploySerializer(serializers.Serializer):
    mesh_id = serializers.UUIDField()
    db_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    admin_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    replication_password = serializers.CharField(
        write_only=True, required=True, allow_blank=False,
        help_text="Strong unique password for replication user"
    )

    def validate_replication_password(self, value):
        if not value or value.strip().lower() == "repl_pass":
            raise serializers.ValidationError(
                "replication_password must be provided and cannot use the default 'repl_pass'."
            )
        return value

    def validate(self, attrs):
        for field in ("db_password", "admin_password"):
            if not str(attrs.get(field, "")).strip():
                raise serializers.ValidationError({field: f"{field} is required."})
        return attrs


class FailoverSerializer(serializers.Serializer):
    target_wg_address = serializers.CharField()

    def validate_target_wg_address(self, value):
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise serializers.ValidationError("target_wg_address must be an IP address.") from exc
        return value


class ConnectReplicaPreflightSerializer(serializers.Serializer):
    mesh_id = serializers.UUIDField()
    target_wg_address = serializers.CharField()

    def validate_target_wg_address(self, value):
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise serializers.ValidationError("target_wg_address must be an IP address.") from exc
        return value


class ConnectReplicaSerializer(serializers.Serializer):
    mesh_id = serializers.UUIDField()
    target_wg_address = serializers.CharField()
    db_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    admin_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    replication_password = serializers.CharField(
        write_only=True, required=True, allow_blank=False,
        help_text="Strong unique password for replication user"
    )

    def validate_target_wg_address(self, value):
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise serializers.ValidationError("target_wg_address must be an IP address.") from exc
        return value

    def validate_replication_password(self, value):
        if not value or value.strip().lower() == "repl_pass":
            raise serializers.ValidationError(
                "replication_password must be provided and cannot use the default 'repl_pass'."
            )
        return value

    def validate(self, attrs):
        for field in ("db_password", "admin_password"):
            if not str(attrs.get(field, "")).strip():
                raise serializers.ValidationError({field: f"{field} is required."})
        return attrs


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ReplicationViewSet(viewsets.ViewSet):
    """
    Manage database replication across the server mesh.

    Actions:
    - deploy:       Deploy Patroni + etcd + HAProxy to all mesh peers
    - health:       Check replication health and lag
    - failover:     Manual failover to a specific replica
    - reinitialize: Rebuild a failed replica from scratch
    """

    permission_classes = [IsAdminUser]

    @action(detail=False, methods=["post"])
    def deploy(self, request):
        """Deploy Patroni replication cluster to a mesh."""
        ser = ReplicationDeploySerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        mesh_id = str(ser.validated_data["mesh_id"])

        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if mesh.peers.filter(is_active=True).count() < 2:
            return Response(
                {"error": "Need at least 2 peers for replication"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if mesh.mesh_status != "ACTIVE":
            return Response(
                {"error": "WireGuard mesh must be ACTIVE before enabling replication."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if mesh.replication_status == "DEPLOYING":
            return Response(
                {"error": "Replication deployment already in progress."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            ReplicationService.validate_mesh_for_replication(mesh)
        except Exception as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Launch async deployment
        from .tasks_replication import deploy_replication_task
        mesh.replication_status = "DEPLOYING"
        mesh.replication_last_error = ""
        mesh.replication_last_result = {}
        mesh.save(update_fields=[
            "replication_status",
            "replication_last_error",
            "replication_last_result",
            "updated_at",
        ])
        try:
            deploy_replication_task.delay(
                mesh_id,
                ser.validated_data["db_password"],
                ser.validated_data["admin_password"],
                ser.validated_data["replication_password"],
            )
        except Exception as exc:
            logger.exception("Failed to queue replication deployment for mesh %s: %s", mesh.id, exc)
            mesh.replication_status = "FAILED"
            mesh.replication_last_error = "Replication deployment could not be queued."
            mesh.save(update_fields=[
                "replication_status",
                "replication_last_error",
                "updated_at",
            ])
            return Response(
                {"error": mesh.replication_last_error},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            "status": "Deployment started",
            "replication_status": mesh.replication_status,
            "mesh": mesh.name,
            "peer_count": mesh.peers.filter(is_active=True).count(),
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["post"], url_path="enable")
    def enable(self, request):
        """Enable replication by deploying the Patroni cluster."""
        return self.deploy(request)

    @action(detail=False, methods=["post"], url_path="sync-now")
    def sync_now(self, request):
        """Refresh replication state immediately and persist visible status."""
        mesh_id = request.data.get("mesh_id")
        if not mesh_id:
            return Response(
                {"error": "mesh_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            result = ReplicationService.sync_now(mesh)
        except Exception as exc:
            logger.exception("Replication sync failed for mesh %s: %s", mesh.id, exc)
            return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(result)

    @action(detail=False, methods=["post"], url_path="disable")
    def disable(self, request):
        """Disable Patroni replication on every peer in the mesh."""
        mesh_id = request.data.get("mesh_id")
        if not mesh_id:
            return Response(
                {"error": "mesh_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if mesh.replication_status == "DEPLOYING":
            return Response(
                {"error": "Replication deployment is in progress."},
                status=status.HTTP_409_CONFLICT,
            )

        result = ReplicationService.disable_replication(mesh)
        return Response({
            "status": mesh.replication_status,
            "result": result,
            "error": mesh.replication_last_error,
        })

    @action(detail=False, methods=["get"], url_path="health/(?P<mesh_id>[^/.]+)")
    def health(self, request, mesh_id=None):
        """Check replication health for a specific mesh."""
        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            health = ReplicationService.check_replication_health(mesh)
            health["local_replicas"] = _get_local_replica_health()
        except Exception as exc:
            logger.exception("Replication health failed for mesh %s: %s", mesh.id, exc)
            health = {"error": str(exc), "local_replicas": _get_local_replica_health()}
        return Response(health)

    @action(detail=False, methods=["post"])
    def failover(self, request):
        """Trigger manual failover to a specific replica."""
        mesh_id = request.data.get("mesh_id")
        target = request.data.get("target_wg_address")

        if not mesh_id or not target:
            return Response(
                {"error": "mesh_id and target_wg_address are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        ser = FailoverSerializer(data={"target_wg_address": target})
        ser.is_valid(raise_exception=True)

        from .tasks_replication import manual_failover_task
        manual_failover_task.delay(str(mesh_id), ser.validated_data["target_wg_address"])

        return Response({
            "status": "Failover initiated",
            "target": ser.validated_data["target_wg_address"],
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=["post"])
    def preflight(self, request):
        """Run pre-flight checks for a new replica."""
        ser = ConnectReplicaPreflightSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            mesh = MeshNetwork.objects.get(id=ser.validated_data["mesh_id"])
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            result = ReplicationService.preflight_check(
                mesh, ser.validated_data["target_wg_address"]
            )
            return Response(result)
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=False, methods=["post"])
    def connect_replica(self, request):
        """Finalize the connection of a new replica."""
        ser = ConnectReplicaSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            mesh = MeshNetwork.objects.get(id=ser.validated_data["mesh_id"])
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            if mesh.replication_status == "DEPLOYING":
                return Response(
                    {"error": "Replication deployment already in progress."},
                    status=status.HTTP_409_CONFLICT,
                )
            result = ReplicationService.connect_replica(
                mesh,
                ser.validated_data["target_wg_address"],
                ser.validated_data["db_password"],
                ser.validated_data["admin_password"],
                ser.validated_data["replication_password"]
            )
            return Response(
                {"status": "Replica connected successfully", "result": result}
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"])
    def reinitialize(self, request):
        """Reinitialize a failed/lagging replica."""
        mesh_id = request.data.get("mesh_id")
        target = request.data.get("target_wg_address")

        if not mesh_id or not target:
            return Response(
                {"error": "mesh_id and target_wg_address are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
            ser = FailoverSerializer(data={"target_wg_address": target})
            ser.is_valid(raise_exception=True)
            result = ReplicationService.reinitialize_replica(
                mesh,
                ser.validated_data["target_wg_address"],
            )
            return Response(result)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
