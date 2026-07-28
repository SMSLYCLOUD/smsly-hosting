import logging
import os
import uuid

from apps.deployments.models import (
    ManagedServer,
)

from .auth import AuthMixin
from .client import RemoteClientMixin, _safe_error_snippet
from .error_handling import ErrorHandlingMixin
from .service_sync import ServiceSyncMixin
from .deployment import DeploymentMixin
from .deletion import DeletionMixin
from .health import HealthMixin

logger = logging.getLogger(__name__)

_ENFORCE_TLS = os.environ.get("SMSLY_ENFORCE_INTERSERVER_TLS", "true").lower() in (
    "1", "true", "yes", "on",
)
_REMOTE_VERIFY = os.environ.get("SMSLY_REMOTE_VERIFY", "true").lower() not in (
    "0", "false", "no", "off",
)


class RemoteOrchestrator(
    RemoteClientMixin,
    AuthMixin,
    ErrorHandlingMixin,
    ServiceSyncMixin,
    DeploymentMixin,
    DeletionMixin,
    HealthMixin,
):
    def __init__(self, server: ManagedServer):
        try:
            fresh = ManagedServer.objects.only(
                "api_token", "gateway_secret", "api_url", "host",
                "ssh_key", "ssh_password", "ssh_user", "ssh_port",
            ).get(id=server.id if isinstance(server.id, uuid.UUID) else server.pk)
            self.server = fresh
        except Exception:
            self.server = server
        self.base_url = (self.server.api_url or f"http://{self.server.host}").rstrip('/')
        self.last_error = ""
        logger.info(
            "RemoteOrchestrator initialized for %s (%s)",
            self.server.name, self.server.host,
        )

    def _set_last_error(self, message: str, response=None):
        detail = _safe_error_snippet(message)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            text = _safe_error_snippet(getattr(response, "text", ""))
            if text:
                detail = f"{detail} Response: {text}"
            if status_code and f"HTTP {status_code}" not in detail:
                detail = f"HTTP {status_code}: {detail}"
        self.last_error = detail

    def describe_last_error(self) -> str:
        return self.last_error
