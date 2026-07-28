"""Build and environment helper functions."""
from __future__ import annotations

try:
    from apps.intelligence.models import AIProviderSettings as _AIProviderSettings
except (ImportError, RuntimeError):
    _AIProviderSettings = None
AIProviderSettings = _AIProviderSettings

from .build_compose import *  # noqa: F401, F403
from .build_docker import *  # noqa: F401, F403
from .build_nixpacks import *  # noqa: F401, F403
