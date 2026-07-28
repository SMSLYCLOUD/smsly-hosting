import logging
import re

from .config import ConfigMixin
from .helpers import HelpersMixin
from .keys import KeyGenMixin
from .mesh import MeshMixin
from .peers import PeersMixin

logger = logging.getLogger(__name__)


class WireGuardService(
    KeyGenMixin,
    HelpersMixin,
    ConfigMixin,
    PeersMixin,
    MeshMixin,
):
    """Manage WireGuard mesh network across Grid servers."""


__all__ = ["WireGuardService"]
