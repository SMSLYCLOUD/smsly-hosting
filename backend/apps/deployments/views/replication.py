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

from ..models.mesh import MeshNetwork
from ..services.replication_service import ReplicationService

logger = logging.getLogger(__name__)


def _get_local_replica_health():
    """Return health status of postgres-replica containers (local HA stack)."""
    from ..models.database_replica import DatabaseReplica
    from ..services import database_replica_service as svc

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
        if not value or value.strip().lower() in ("repl_pass", "repl_change_me"):
            raise serializers.ValidationError(
                "replication_password must be a strong unique password. "
                "Default passwords like 'repl_pass' or 'repl_change_me' are not allowed."
            )
        if len(value.strip()) < 16:
            raise serializers.ValidationError(
                "replication_password must be at least 16 characters."
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
        if not value or value.strip().lower() in ("repl_pass", "repl_change_me"):
            raise serializers.ValidationError(
                "replication_password must be a strong unique password. "
                "Default passwords like 'repl_pass' or 'repl_change_me' are not allowed."
            )
        if len(value.strip()) < 16:
            raise serializers.ValidationError(
                "replication_password must be at least 16 characters."
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
        from ..services.task_encryption import encrypt_arg
        from ..tasks.data.tasks_replication import deploy_replication_task
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
                encrypt_arg(ser.validated_data["db_password"]),
                encrypt_arg(ser.validated_data["admin_password"]),
                encrypt_arg(ser.validated_data["replication_password"]),
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
            mesh = MeshNetwork.objects.get(id=mesh_id)
        except MeshNetwork.DoesNotExist:
            return Response(
                {"error": "Mesh not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if mesh.owner and mesh.owner != request.user and not request.user.is_staff:
            return Response({'error': 'You do not own this mesh'}, status=status.HTTP_403_FORBIDDEN)

        ser = FailoverSerializer(data={"target_wg_address": target})
        ser.is_valid(raise_exception=True)

        from ..tasks.data.tasks_replication import manual_failover_task
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

    @action(detail=False, methods=["get"], url_path="local-health")
    def local_health(self, request):
        """Return health status of local postgres-replica containers.

        This endpoint does NOT require a mesh — it reports on the local
        HA stack (primary + replica) running as Docker containers on the
        same host.
        """
        local_replicas = _get_local_replica_health()

        # Also probe the primary directly so the page can show both sides
        primary_info = {"name": "smsly-postgres-primary", "status": "UNKNOWN"}
        try:
            from ..services import database_replica_service as svc

            class _PrimaryProbe:
                host = "smsly-postgres-primary"
                port = 5432
                username = "postgres"
                password = ""
                database = "smsly_hosting"
                ssl_mode = "disable"

            ok, err, _lag = svc.test_connection(_PrimaryProbe())
            primary_info["status"] = "OK" if ok else f"ERROR: {err}"
        except Exception as exc:
            primary_info["status"] = f"ERROR: {exc}"

        return Response({
            "primary": primary_info,
            "local_replicas": local_replicas,
        })

    @action(detail=False, methods=["get"], url_path="redis-health")
    def redis_health(self, request):
        """Return Redis HA health status (primary, replica, sentinels).

        This endpoint does NOT require a mesh — it reports on the local
        Redis HA stack running as Docker containers on the same host.
        """
        import os
        from redis.sentinel import Sentinel

        REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
        SENTINEL_PASSWORD = os.environ.get("SENTINEL_PASSWORD", "")
        SENTINEL_HOSTS = [
            ("redis-sentinel-1", 26379),
            ("redis-sentinel-2", 26379),
            ("redis-sentinel-3", 26379),
        ]

        result = {
            "primary": {"name": "redis-primary", "status": "UNKNOWN", "role": None, "connected_slaves": 0},
            "replica": {"name": "redis-replica", "status": "UNKNOWN", "role": None, "master_link_status": None, "lag_seconds": None},
            "sentinels": [],
        }

        try:
            sentinel = Sentinel(SENTINEL_HOSTS, socket_timeout=3, sentinel_kwargs={"password": SENTINEL_PASSWORD} if SENTINEL_PASSWORD else None)

            # Discover master
            master_addr = sentinel.discover_master("mymaster")
            result["primary"]["name"] = f"redis-primary ({master_addr[0]}:{master_addr[1]})"

            # Connect to master
            master = sentinel.master_for("mymaster", socket_timeout=3, password=REDIS_PASSWORD)
            info = master.info("replication")
            result["primary"]["role"] = info.get("role")
            result["primary"]["connected_slaves"] = info.get("connected_slaves", 0)
            result["primary"]["status"] = "OK" if info.get("role") == "master" else f"UNEXPECTED: {info.get('role')}"

            # Connect to replica
            replica = sentinel.slave_for("mymaster", socket_timeout=3, password=REDIS_PASSWORD)
            rinfo = replica.info("replication")
            result["replica"]["role"] = rinfo.get("role")
            result["replica"]["master_link_status"] = rinfo.get("master_link_status")
            result["replica"]["lag_seconds"] = rinfo.get("master_last_io_seconds_ago")
            result["replica"]["status"] = (
                "OK" if rinfo.get("role") == "slave" and rinfo.get("master_link_status") == "up"
                else f"ERROR: role={rinfo.get('role')} link={rinfo.get('master_link_status')}"
            )

            master.close()
            replica.close()

            # Sentinel info
            for i, (host, port) in enumerate(SENTINEL_HOSTS):
                try:
                    # Sentinel state via redis-cli-like commands via Sentinel
                    s = Sentinel([(host, port)], socket_timeout=2,
                                 sentinel_kwargs={"password": SENTINEL_PASSWORD} if SENTINEL_PASSWORD else None)
                    s.discover_master("mymaster")
                    result["sentinels"].append({
                        "name": host,
                        "status": "OK",
                        "ip": host,
                        "port": port,
                    })
                except Exception as exc:
                    result["sentinels"].append({
                        "name": host,
                        "status": f"ERROR: {exc}",
                        "ip": host,
                        "port": port,
                    })

        except Exception as exc:
            result["primary"]["status"] = f"ERROR: {exc}"
            result["replica"]["status"] = f"ERROR: {exc}"

        return Response(result)

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
