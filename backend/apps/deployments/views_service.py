import logging

logger = logging.getLogger(__name__)
import logging  # noqa: E402

from rest_framework import viewsets  # noqa: E402

from .views_ai_router import ServiceAIRouterActionsMixin  # noqa: E402
from .views_domains import ServiceDomainActionsMixin  # noqa: E402
from .views_envvars import ServiceEnvVarActionsMixin  # noqa: E402
from .views_files import ServiceFileActionsMixin  # noqa: E402


class ServiceViewSet(ServiceFileActionsMixin, ServiceEnvVarActionsMixin, ServiceDomainActionsMixin, ServiceAIRouterActionsMixin, viewsets.ModelViewSet):
    """Combined ViewSet for services, composed from domain-specific mixins."""
