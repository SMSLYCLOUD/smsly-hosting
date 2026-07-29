from .core import ManagedServerViewSet as _Base
from .provisioning import ProvisioningMixin
from .health import HealthMixin
from .agent import AgentMixin
from .proxy import ProxyMixin
from .resources import ResourcesMixin
from .healing import HealingMixin
from .serializers import (  # noqa: F401
    ManagedServerCreateSerializer,
    ManagedServerProvisionSerializer,
    ManagedServerSerializer,
    ServerCheckAllThrottle,
    ServerCommandThrottle,
    ServerHealThrottle,
    ServerProvisionThrottle,
    ServerProxyThrottle,
)


class ManagedServerViewSet(
    ProvisioningMixin,
    HealthMixin,
    AgentMixin,
    ProxyMixin,
    ResourcesMixin,
    HealingMixin,
    _Base,
):
    pass


del _Base
