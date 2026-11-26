"""Build and environment helper functions."""
from __future__ import annotations

import docker  # noqa: F401
from .health import _wait_for_local_container_healthy  # noqa: F401

try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None
AIProviderSettings = _AIProviderSettings

from .build_compose import *  # noqa: F401, F403
from .build_docker import *  # noqa: F401, F403
from .build_nixpacks import *  # noqa: F401, F403

import subprocess as _real_subprocess
import types as _types

subprocess = _types.SimpleNamespace(run=_real_subprocess.run)
