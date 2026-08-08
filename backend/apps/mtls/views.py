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

from .models import MtlsAuthorizationPolicy, MtlsConfig

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
SPIRE_SERVER_SOCKET = "/tmp/spire-server/private/api.sock"
SPIRE_AGENT_SOCKET = "/opt/spire/run/agent.sock"


def _user_can_access_service(user, service) -> bool:
    """Check if a user can manage a service."""
    if user.is_superuser:
        return True
    if service.owner == user:
        return True
    team = getattr(service.project, 'team', None)
    return bool(team and team.members.filter(user=user).exists())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mtls_status(request, service_id):
    """
    GET /api/v1/services/{service_id}/mtls/status

    Returns mTLS status for a service.
    """
    from apps.deployments.models import Service
    service = get_object_or_404(Service, id=service_id)
    if not _user_can_access_service(request.user, service):
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    config = get_object_or_404(MtlsConfig, service=service)
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

    Enables mTLS for a service. For running services, automatically
    injects SPIRE mounts via container hot-swap. For stopped services,
    SPIRE mounts will be included on next deploy.
    """
    from apps.deployments.models import Service
    service = get_object_or_404(Service, id=service_id)
    if not _user_can_access_service(request.user, service):
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    config, created = MtlsConfig.objects.get_or_create(
        service=service,
        defaults={"enabled": True, "trust_domain": "ecosystem.local"},
    )
    if not created:
        config.enabled = True
        if not config.trust_domain:
            config.trust_domain = "ecosystem.local"
        config.save()

    # Auto-inject SPIRE mounts into running containers
    injected = False
    if service.status == "RUNNING":
        try:
            from apps.deployments.services.mtls_integration import (
                get_mtls_labels,
                get_mtls_env_vars,
                get_mtls_docker_run_volumes,
            )
            from apps.cloud.docker_client import get_docker_client

            docker_client = get_docker_client()
            containers = docker_client.containers.list(
                filters={"label": "managed_by=smsly-hosting"},
            )

            # Find containers for this service
            service_containers = [
                c for c in containers
                if (c.labels or {}).get("smsly.blue_green.canonical_name") == service.name
            ]

            if service_containers:
                # Trigger background hot-swap via Celery
                from .tasks import inject_mtls_task
                inject_mtls_task.delay(str(service.id))
                injected = True
        except Exception as e:
            logger.warning("Auto-injection scheduling failed for %s: %s", service.name, e)

    message = "mTLS enabled."
    if injected:
        message += " SPIRE mounts are being injected into running containers (hot-swap in progress)."
    elif service.status == "RUNNING":
        message += " Redeploy the service for SPIRE mounts to take effect."
    else:
        message += " SPIRE mounts will be included on next deploy."

    return Response({
        "status": "enabled",
        "spiffe_id": config.spiffe_id,
        "trust_domain": config.trust_domain,
        "message": message,
        "auto_injected": injected,
        "requires_redeploy": not injected and service.status == "RUNNING",
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mtls_disable(request, service_id):
    """
    POST /api/v1/services/{service_id}/mtls/disable

    Disables mTLS for a service. The service must be redeployed for
    the SPIRE socket mount to be removed.
    """
    from apps.deployments.models import Service
    service = get_object_or_404(Service, id=service_id)
    if not _user_can_access_service(request.user, service):
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    config = get_object_or_404(MtlsConfig, service=service)
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
    platform and ecosystem SPIRE servers. Admin only.
    """
    if not request.user.is_superuser:
        return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

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

    platform_server_healthy = _check_spire(PLATFORM_SPIRE_SERVER_CONTAINER, SPIRE_SERVER_SOCKET)
    platform_agent_healthy = _check_agent(PLATFORM_SPIRE_AGENT_CONTAINER, SPIRE_AGENT_SOCKET)
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

    Returns mTLS configurations for services the user can access.
    """
    from apps.deployments.models import Service

    if request.user.is_superuser:
        configs = MtlsConfig.objects.select_related("service").all()
    else:
        accessible_service_ids = Service.objects.filter(
            models_Q_owner_or_team(request.user)
        ).values_list("id", flat=True)
        configs = MtlsConfig.objects.select_related("service").filter(
            service_id__in=accessible_service_ids
        )

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


def models_Q_owner_or_team(user):
    """Build a Q filter for services owned by or team-accessible to user."""
    from django.db.models import Q
    from apps.deployments.models import Service

    return Q(owner=user) | Q(project__team__members__user=user)


# ---------------------------------------------------------------------------
# Authorization Policy CRUD
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def policy_list_create(request):
    """
    GET  /api/v1/mtls/policies/?service_id=<uuid>   — list policies
    GET  /api/v1/mtls/policies/?service_id=*         — list all policies
    POST /api/v1/mtls/policies/                      — create policy
    """
    from apps.deployments.models import Service
    from django.db.models import Q

    if request.method == "POST":
        data = request.data
        service_id = data.get("target_service_id")
        if not service_id:
            return Response(
                {"detail": "target_service_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = get_object_or_404(Service, id=service_id)
        if not _user_can_access_service(request.user, service):
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        name = data.get("name", "").strip()
        source_spiffe_id = data.get("source_spiffe_id", "").strip()
        if not name or not source_spiffe_id:
            return Response(
                {"detail": "name and source_spiffe_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if source_spiffe_id != "*" and not source_spiffe_id.startswith("spiffe://"):
            return Response(
                {"detail": "source_spiffe_id must start with 'spiffe://' or be '*'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = data.get("action", MtlsAuthorizationPolicy.Action.ALLOW)
        if action not in dict(MtlsAuthorizationPolicy.Action.choices):
            return Response(
                {"detail": f"Invalid action. Must be one of: {dict(MtlsAuthorizationPolicy.Action.choices)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        methods = [m.upper() for m in data.get("methods", [])]

        policy = MtlsAuthorizationPolicy.objects.create(
            name=name,
            source_spiffe_id=source_spiffe_id,
            target_service=service,
            paths=data.get("paths", []),
            methods=methods,
            action=action,
            priority=data.get("priority", 0),
            enabled=data.get("enabled", True),
        )

        return Response({
            "id": policy.id,
            "name": policy.name,
            "source_spiffe_id": policy.source_spiffe_id,
            "target_service_id": str(policy.target_service_id),
            "target_service_name": policy.target_service.name,
            "paths": policy.paths,
            "methods": policy.methods,
            "action": policy.action,
            "priority": policy.priority,
            "enabled": policy.enabled,
            "created_at": policy.created_at.isoformat(),
            "updated_at": policy.updated_at.isoformat(),
        }, status=status.HTTP_201_CREATED)

    # GET
    service_id = request.query_params.get("service_id")
    if not service_id:
        return Response(
            {"detail": "service_id query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    accessible_service_ids = Service.objects.filter(
        Q(owner=request.user) | Q(project__team__members__user=request.user)
    ).values_list("id", flat=True)

    if service_id == "*":
        policies = MtlsAuthorizationPolicy.objects.filter(
            target_service_id__in=accessible_service_ids
        ).order_by("-priority", "id")
    else:
        service = get_object_or_404(Service, id=service_id)
        if not _user_can_access_service(request.user, service):
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
        policies = MtlsAuthorizationPolicy.objects.filter(
            target_service=service
        ).order_by("-priority", "id")

    return Response([
        {
            "id": p.id,
            "name": p.name,
            "source_spiffe_id": p.source_spiffe_id,
            "target_service_id": str(p.target_service_id),
            "target_service_name": p.target_service.name,
            "paths": p.paths,
            "methods": p.methods,
            "action": p.action,
            "priority": p.priority,
            "enabled": p.enabled,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }
        for p in policies
    ])


@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def policy_update_delete(request, policy_id):
    """
    PUT/PATCH /api/v1/mtls/policies/<id>/   — update policy
    DELETE   /api/v1/mtls/policies/<id>/   — delete policy
    """
    policy = get_object_or_404(MtlsAuthorizationPolicy, id=policy_id)
    service = policy.target_service

    if not _user_can_access_service(request.user, service):
        return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "DELETE":
        policy.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # PUT / PATCH
    data = request.data
    if "name" in data:
        policy.name = data["name"]
    if "source_spiffe_id" in data:
        source = data["source_spiffe_id"].strip()
        if source != "*" and not source.startswith("spiffe://"):
            return Response(
                {"detail": "source_spiffe_id must start with 'spiffe://' or be '*'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        policy.source_spiffe_id = source
    if "paths" in data:
        policy.paths = data["paths"]
    if "methods" in data:
        policy.methods = [m.upper() for m in data["methods"]]
    if "action" in data:
        if data["action"] not in dict(MtlsAuthorizationPolicy.Action.choices):
            return Response(
                {"detail": f"Invalid action. Must be one of: {dict(MtlsAuthorizationPolicy.Action.choices)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        policy.action = data["action"]
    if "priority" in data:
        policy.priority = data["priority"]
    if "enabled" in data:
        policy.enabled = data["enabled"]

    policy.save()

    return Response({
        "id": policy.id,
        "name": policy.name,
        "source_spiffe_id": policy.source_spiffe_id,
        "target_service_id": str(policy.target_service_id),
        "target_service_name": policy.target_service.name,
        "paths": policy.paths,
        "methods": policy.methods,
        "action": policy.action,
        "priority": policy.priority,
        "enabled": policy.enabled,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    })
