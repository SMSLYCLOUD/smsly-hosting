import logging

logger = logging.getLogger(__name__)
import logging

from rest_framework import viewsets

from .views_ai_router import ServiceAIRouterActionsMixin
from .views_domains import ServiceDomainActionsMixin
from .views_envvars import ServiceEnvVarActionsMixin
from .views_files import ServiceFileActionsMixin


class ServiceViewSet(ServiceFileActionsMixin, ServiceEnvVarActionsMixin, ServiceDomainActionsMixin, ServiceAIRouterActionsMixin, viewsets.ModelViewSet):
    """Combined ViewSet for services, composed from domain-specific mixins."""
