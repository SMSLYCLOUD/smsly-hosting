import os
import ipaddress
from urllib.parse import urlparse

from .manager import RemoteOrchestrator
from .client import (
    RemoteClientMixin,
    REMOTE_RESPONSE_SNIPPET_CHARS,
    SAFE_METHODS,
    _host_is_ip,
    _is_node_server,
    _is_internal_target,
    _safe_error_snippet,
)
from .auth import AuthMixin
from .error_handling import ErrorHandlingMixin
from .service_sync import ServiceSyncMixin
from .deployment import DeploymentMixin
from .deletion import DeletionMixin
from .health import HealthMixin

_ENFORCE_TLS = os.environ.get("SMSLY_ENFORCE_INTERSERVER_TLS", "true").lower() in (
    "1", "true", "yes", "on",
)
_REMOTE_VERIFY = os.environ.get("SMSLY_REMOTE_VERIFY", "true").lower() not in (
    "0", "false", "no", "off",
)

__all__ = [
    "RemoteOrchestrator",
    "RemoteClientMixin",
    "AuthMixin",
    "ErrorHandlingMixin",
    "ServiceSyncMixin",
    "DeploymentMixin",
    "DeletionMixin",
    "HealthMixin",
    "REMOTE_RESPONSE_SNIPPET_CHARS",
    "SAFE_METHODS",
    "_ENFORCE_TLS",
    "_REMOTE_VERIFY",
    "_host_is_ip",
    "_is_node_server",
    "_is_internal_target",
    "_safe_error_snippet",
]
