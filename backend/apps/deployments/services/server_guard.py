from apps.deployments.models_core import ManagedServer

class ServerGuard:
    """Enforces constraint that primary server cannot run user workloads."""

    @classmethod
    def is_primary(cls, server_id: str) -> bool:
        """Returns True if the given server is the primary/control-plane server."""
        try:
            server = ManagedServer.objects.get(id=server_id)
            return server.is_primary
        except ManagedServer.DoesNotExist:
            return False

    @classmethod
    def assert_not_primary(cls, server_id: str):
        """Raises a safe exception or returns an error format if the server is primary."""
        if cls.is_primary(server_id):
             return {
                "ok": False,
                "error": {
                    "code": "PRIMARY_SERVER_DEPLOYMENT_BLOCKED",
                    "message": "Primary/control-plane server cannot be used for user deployments.",
                    "details": {
                        "server_id": str(server_id)
                    }
                }
            }
        return {"ok": True}
