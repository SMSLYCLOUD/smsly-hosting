"""
mTLS Management Views
=====================
API endpoints for managing SPIFFE mTLS per service.
"""

import os
import subprocess
import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import MtlsConfig

logger = logging.getLogger(__name__)

ECOSYSTEM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_ECOSYSTEM_SERVER_CONTAINER", "smsly-spire-server-ecosystem"
)
PLATFORM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_SERVER_CONTAINER", "smsly-spire-server"
)
ECOSYSTEM_SPIRE_AGENT_CONTAINER = os.getenv(
    "SPIRE_ECOSYSTEM_AGENT_CONTAINER", "smsly-spire-agent-ecosystem"
)
PLATFORM_SPIRE_AGENT_CONTAINER = os.getenv(
    "SPIRE_AGENT_CONTAINER", "smsly-spire-agent"
)
SPIRE_SERVER_SOCKET = "/opt/spire/data/server.sock"
SPIRE_AGENT_SOCKET = "/opt/spire/run/agent.sock"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mtls_status(request, service_id):
    """
    GET /api/v1/services/{service_id}/mtls/status

    Returns mTLS status for a service.
    """
    config = get_object_or_404(MtlsConfig, service_id=service_id)
    return Response({
        "service_id": str(config.service_id),
        "service_name": config.service.name,
        "mtls_enabled": config.enabled,
        "trust_domain": config.trust_domain,
        "spiffe_id": config.spiffe_id,
        "svid_expiry": config.svid_expiry.isoformat() if config.svid_expiry else None,
        "svid_ttl_remaining": config.svid_ttl_remaining,
        "is_svid_expired": config.is_svid_expired,
        "last_rotation": config.last_rotation.isoformat() if config.last_rotation else None,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mtls_enable(request, service_id):
    """
    POST /api/v1/services/{service_id}/mtls/enable

    Enables mTLS for a service. The service must be redeployed for
    SPIRE socket mounts, labels, and env vars to take effect.
    """
    config, created = MtlsConfig.objects.get_or_create(
        service_id=service_id,
        defaults={"enabled": True, "trust_domain": "ecosystem.local"},
    )
    if not created:
        config.enabled = True
        if not config.trust_domain:
            config.trust_domain = "ecosystem.local"
        config.save()

    return Response({
        "status": "enabled",
        "spiffe_id": config.spiffe_id,
        "trust_domain": config.trust_domain,
        "message": "mTLS enabled. Redeploy the service for SPIRE mounts to take effect.",
        "requires_redeploy": True,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mtls_disable(request, service_id):
    """
    POST /api/v1/services/{service_id}/mtls/disable

    Disables mTLS for a service. The service must be redeployed for
    the SPIRE socket mount to be removed.
    """
    config = get_object_or_404(MtlsConfig, service_id=service_id)
    config.enabled = False
    config.save()

    return Response({
        "status": "disabled",
        "message": "mTLS disabled. Redeploy the service to remove SPIRE mounts.",
        "requires_redeploy": True,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mtls_health(request):
    """
    GET /api/v1/mtls/health

    Returns platform-wide mTLS health status including both
    platform and ecosystem SPIRE servers.
    """

    def _check_spire(container, socket_path):
        try:
            result = subprocess.run(
                ["docker", "exec", container, "/opt/spire/bin/spire-server",
                 "healthcheck", "-socketPath", socket_path],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _check_agent(container, socket_path):
        try:
            result = subprocess.run(
                ["docker", "exec", container, "/opt/spire/bin/spire-agent",
                 "healthcheck", "-socketPath", socket_path],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    # Platform SPIRE
    platform_server_healthy = _check_spire(PLATFORM_SPIRE_SERVER_CONTAINER, SPIRE_SERVER_SOCKET)
    platform_agent_healthy = _check_agent(PLATFORM_SPIRE_AGENT_CONTAINER, SPIRE_AGENT_SOCKET)

    # Ecosystem SPIRE
    ecosystem_server_healthy = _check_spire(ECOSYSTEM_SPIRE_SERVER_CONTAINER, SPIRE_SERVER_SOCKET)
    ecosystem_agent_healthy = _check_agent(ECOSYSTEM_SPIRE_AGENT_CONTAINER, SPIRE_AGENT_SOCKET)

    total_services = MtlsConfig.objects.count()
    enabled_services = MtlsConfig.objects.filter(enabled=True).count()
    expired_svids = MtlsConfig.objects.filter(
        enabled=True, svid_expiry__lt=timezone.now()
    ).count()

    return Response({
        "platform": {
            "spire_server_healthy": platform_server_healthy,
            "spire_agent_healthy": platform_agent_healthy,
            "trust_domain": os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local"),
        },
        "ecosystem": {
            "spire_server_healthy": ecosystem_server_healthy,
            "spire_agent_healthy": ecosystem_agent_healthy,
            "trust_domain": os.getenv("ECOSYSTEM_TRUST_DOMAIN", "ecosystem.local"),
        },
        "total_services": total_services,
        "mtls_enabled_services": enabled_services,
        "expired_svids": expired_svids,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mtls_list(request):
    """
    GET /api/v1/mtls/configs

    Returns all mTLS configurations.
    """
    configs = MtlsConfig.objects.select_related("service").all()
    return Response([
        {
            "service_id": str(c.service_id),
            "service_name": c.service.name,
            "mtls_enabled": c.enabled,
            "trust_domain": c.trust_domain,
            "spiffe_id": c.spiffe_id,
            "svid_expiry": c.svid_expiry.isoformat() if c.svid_expiry else None,
            "is_svid_expired": c.is_svid_expired,
        }
        for c in configs
    ])
