from typing import Any

from .addons import build_addon_provisioning_requests
from .constants import generate_strong_secret
from .core import CoreMixin
from .detection import DetectionMixin
from .file_helpers import FileHelpersMixin
from .injection import InjectionMixin
from .parsers import ParsersMixin
from .secrets import SecretsMixin


class ManifestEnvResolver(
    CoreMixin,
    ParsersMixin,
    InjectionMixin,
    SecretsMixin,
    DetectionMixin,
    FileHelpersMixin,
):
    def __init__(
        self,
        source_dir: str | None = None,
        service_name: str = "",
        cross_service_map: dict[str, Any] | None = None,
    ):
        self.source_dir = source_dir
        self.service_name = service_name
        self.cross_service_map = cross_service_map or {}

        self.is_frontend = False
        self.stack = "python"
        self.port = 8000
        self.env_example_vars: dict[str, str] = {}
        self.secrets_manifest: dict[str, Any] = {"serves_as": [], "expects_from": []}
        self.unresolved_vars: list[str] = []
        self.heuristic_vars: list[str] = []
        self.resolved_env: dict[str, str] = {}


__all__ = [
    "ManifestEnvResolver",
    "build_addon_provisioning_requests",
    "generate_strong_secret",
]
