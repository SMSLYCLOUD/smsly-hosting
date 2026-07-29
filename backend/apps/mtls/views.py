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

    Enables mTLS for a service. The service will be redeployed with SPIRE socket mount.
    """
    config, created = MtlsConfig.objects.get_or_create(
        service_id=service_id,
        defaults={"enabled": True},
    )
    if not created:
        config.enabled = True
        config.save()

    return Response({
        "status": "enabled",
        "spiffe_id": config.spiffe_id,
        "message": "mTLS enabled. Service will be redeployed with SPIRE socket mount.",
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mtls_disable(request, service_id):
    """
    POST /api/v1/services/{service_id}/mtls/disable

    Disables mTLS for a service. The SPIRE socket mount will be removed on next deploy.
    """
    config = get_object_or_404(MtlsConfig, service_id=service_id)
    config.enabled = False
    config.save()

    return Response({
        "status": "disabled",
        "message": "mTLS disabled. SPIRE socket mount will be removed on next deploy.",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mtls_health(request):
    """
    GET /api/v1/mtls/health

    Returns platform-wide mTLS health status.
    """

    # Check SPIRE server health
    spire_server_healthy = False
    spire_server_container = os.getenv("SPIRE_SERVER_CONTAINER", "smsly-spire-server")
    spire_server_socket = os.getenv("SPIRE_SERVER_SOCKET", "/opt/spire/data/server.sock")
    try:
        result = subprocess.run(
            ["docker", "exec", spire_server_container, "/opt/spire/bin/spire-server",
             "healthcheck", "-socketPath", spire_server_socket],
            capture_output=True, timeout=10,
        )
        spire_server_healthy = result.returncode == 0
    except Exception:
        pass

    # Check SPIRE agent health
    spire_agent_healthy = False
    spire_agent_container = os.getenv("SPIRE_AGENT_CONTAINER", "smsly-spire-agent")
    spire_agent_socket = os.getenv("SPIRE_AGENT_SOCKET", "/opt/spire/run/agent.sock")
    try:
        result = subprocess.run(
            ["docker", "exec", spire_agent_container, "/opt/spire/bin/spire-agent",
             "healthcheck", "-socketPath", spire_agent_socket],
            capture_output=True, timeout=10,
        )
        spire_agent_healthy = result.returncode == 0
    except Exception:
        pass

    # Count services with mTLS
    total_services = MtlsConfig.objects.count()
    enabled_services = MtlsConfig.objects.filter(enabled=True).count()
    expired_svids = MtlsConfig.objects.filter(
        enabled=True, svid_expiry__lt=timezone.now()
    ).count()

    return Response({
        "spire_server_healthy": spire_server_healthy,
        "spire_agent_healthy": spire_agent_healthy,
        "total_services": total_services,
        "mtls_enabled_services": enabled_services,
        "expired_svids": expired_svids,
        "trust_domain": os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local"),
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
            "spiffe_id": c.spiffe_id,
            "svid_expiry": c.svid_expiry.isoformat() if c.svid_expiry else None,
            "is_svid_expired": c.is_svid_expired,
        }
        for c in configs
    ])
