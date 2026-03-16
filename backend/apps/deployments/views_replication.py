"""
Replication management views.

API endpoints for deploying, monitoring, and managing
Patroni-based PostgreSQL streaming replication.
"""

import logging

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models_mesh import MeshNetwork
from .services.replication_service import ReplicationService

logger = logging.getLogger(__name__)


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


class FailoverSerializer(serializers.Serializer):
    target_wg_address = serializers.CharField()


class ConnectReplicaPreflightSerializer(serializers.Serializer):
    mesh_id = serializers.UUIDField()
    target_wg_address = serializers.CharField()


class ConnectReplicaSerializer(serializers.Serializer):
    mesh_id = serializers.UUIDField()
    target_wg_address = serializers.CharField()
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

        # Launch async deployment
        from .tasks_replication import deploy_replication_task
        deploy_replication_task.delay(
            mesh_id,
            ser.validated_data["db_password"],
            ser.validated_data["admin_password"],
            ser.validated_data.get("replication_password", "repl_pass"),
        )

        return Response({
            "status": "Deployment started",
            "mesh": mesh.name,
            "peer_count": mesh.peers.filter(is_active=True).count(),
        }, status=status.HTTP_202_ACCEPTED)

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

        health = ReplicationService.check_replication_health(mesh)
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
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .tasks_replication import manual_failover_task
        manual_failover_task.delay(str(mesh_id), target)

        return Response({
            "status": "Failover initiated",
            "target": target,
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
            ReplicationService.connect_replica(
                mesh,
                ser.validated_data["target_wg_address"],
                ser.validated_data["db_password"],
                ser.validated_data["admin_password"],
                ser.validated_data["replication_password"]
            )
            return Response(
                {"status": "Replica connected successfully"}
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
            result = ReplicationService.reinitialize_replica(mesh, target)
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
