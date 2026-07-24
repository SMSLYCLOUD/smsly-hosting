from rest_framework.exceptions import ValidationError

from apps.deployments.models.core import ManagedServer


class ServerGuard:
    """Central workload placement guard for control-plane servers."""

    CONTROL_PLANE_VALUES = {"PRIMARY", "CONTROL_PLANE", "CONTROL-PLANE"}

    @classmethod
    def is_control_plane(cls, server) -> bool:
        if not server:
            return False

        from django.conf import settings
        # Global override or specific server flag
        allow_control_plane = getattr(
            settings, 'GRID_ALLOW_CONTROL_PLANE_WORKLOADS',
            getattr(settings, 'CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS', False),
        )
        server_allows = bool(getattr(server, "allow_user_workloads", False))

        is_primary = bool(getattr(server, "is_primary", False))
        role = str(getattr(server, "role", "") or "").upper()
        server_type = str(getattr(server, "server_type", "") or "").upper()

        # If it's a control-plane type server
        if is_primary or role in cls.CONTROL_PLANE_VALUES or server_type in cls.CONTROL_PLANE_VALUES:
            # Allow if settings or server flag says yes
            if allow_control_plane or server_allows:
                return False

            # Resilience: If this is the ONLY online server, we MUST allow workloads to prevent "seamless" deployment deadlocks
            online_count = ManagedServer.objects.filter(status=ManagedServer.Status.ONLINE).count()
            return not online_count <= 1

        # For workers, just respect the flag
        return not bool(getattr(server, "allow_user_workloads", True))

    @classmethod
    def is_primary(cls, server_id: str) -> bool:
        try:
            server = ManagedServer.objects.get(id=server_id)
        except ManagedServer.DoesNotExist:
            return False
        return cls.is_control_plane(server)

    @classmethod
    def error_payload(cls, server):
        return {
            "ok": False,
            "error": {
                "code": "PRIMARY_SERVER_DEPLOYMENT_BLOCKED",
                "message": "Primary/control-plane server cannot be used for user deployments.",
                "details": {"server_id": str(getattr(server, "id", ""))},
            },
        }

    @classmethod
    def assert_user_workload_allowed(cls, server):
        if cls.is_control_plane(server):
            raise ValidationError(cls.error_payload(server))

    @classmethod
    def check_user_workload_allowed(cls, server):
        if cls.is_control_plane(server):
            return cls.error_payload(server)
        return {"ok": True}

    @classmethod
    def filter_user_workload_targets(cls, queryset):
        return queryset.filter(
            is_primary=False,
            allow_user_workloads=True,
            status=ManagedServer.Status.ONLINE
        )

    @classmethod
    def assert_not_primary(cls, server_id: str):
        if cls.is_primary(server_id):
            try:
                server = ManagedServer.objects.get(id=server_id)
            except ManagedServer.DoesNotExist:
                server = None
            return cls.error_payload(server)
        return {"ok": True}
