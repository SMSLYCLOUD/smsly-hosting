from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response

from .services import get_crowdsec_service

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crowdsec_decisions(request: Request) -> Response:
    """List CrowdSec decisions with filters.

    Query params:
      active=true|false (default true)
      ip=<ip>
      scenario=<scenario>
      service_id=<uuid>
      limit=100 (default)
    """
    try:
        service = get_crowdsec_service()
        active = request.query_params.get("active", "true").lower() != "false"
        ip = request.query_params.get("ip") or None
        scenario = request.query_params.get("scenario") or None
        service_id = request.query_params.get("service_id") or None
        limit = int(request.query_params.get("limit", 100))
        if limit > 500:
            limit = 500

        decisions = get_crowdsec_service().get_decisions(
            active=active, ip=ip, scenario=scenario, service_id=service_id, limit=limit
        )

        return Response({
            "count": len(decisions),
            "results": [
                {
                    "id": d.id,
                    "scope": d.scope,
                    "value": d.value,
                    "type": d.type,
                    "origin": d.origin,
                    "scenario": d.scenario,
                    "scenario_version": d.scenario_version,
                    "events_count": d.events_count,
                    "simulated": d.simulated,
                    "start_time": d.start_time,
                    "end_time": d.end_time,
                    "service": d.service,
                    "service_name": get_service_name(d.service) if d.service else None,
                }
                for d in decisions
            ],
        })
    except Exception as exc:
        logger.exception("crowdsec_decisions failed")
        return Response({"error": str(exc)}, status=500)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crowdsec_service_decisions(request: Request, service_id: str) -> Response:
    """Decisions for a specific service."""
    try:
        decisions = get_crowdsec_service().get_service_decisions(service_id, active=True)
        return Response({
            "count": len(decisions),
            "results": [
                {
                    "id": d.id,
                    "scope": d.scope,
                    "value": d.value,
                    "type": d.type,
                    "origin": d.origin,
                    "scenario": d.scenario,
                    "scenario_version": d.scenario_version,
                    "events_count": d.events_count,
                    "simulated": d.simulated,
                    "start_time": d.start_time,
                    "end_time": d.end_time,
                }
                for d in decisions
            ],
        })
    except Exception as exc:
        logger.exception("crowdsec_service_decisions failed")
        return Response({"error": str(exc)}, status=500)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crowdsec_alerts(request: Request) -> Response:
    """List CrowdSec alerts."""
    try:
        limit = int(request.query_params.get("limit", 50))
        alerts = get_crowdsec_service().get_alerts(limit=limit)
        return Response({
            "count": len(alerts),
            "results": [
                {
                    "id": a.id,
                    "source": a.source,
                    "scenario": a.scenario,
                    "scenario_version": a.scenario_version,
                    "scope": a.scope,
                    "value": a.value,
                    "events_count": a.events_count,
                    "start_time": a.start_time,
                    "created_at": a.created_at,
                    "message": a.message,
                    "events": a.events,
                    "service": a.service,
                    "service_name": get_service_name(a.service) if a.service else None,
                }
                for a in alerts
            ],
        })
    except Exception as exc:
        logger.exception("crowdsec_alerts failed")
        return Response({"error": str(exc)}, status=500)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crowdsec_service_alerts(request: Request, service_id: str) -> Response:
    """Alerts for a specific service."""
    try:
        alerts = get_crowdsec_service().get_service_alerts(service_id)
        return Response({
            "count": len(alerts),
            "results": [
                {
                    "id": a.id,
                    "source": a.source,
                    "scenario": a.scenario,
                    "scenario_version": a.scenario_version,
                    "scope": a.scope,
                    "value": a.value,
                    "events_count": a.events_count,
                    "start_time": a.start_time,
                    "created_at": a.created_at,
                    "message": a.message,
                    "events": a.events,
                }
                for a in alerts
            ],
        })
    except Exception as exc:
        logger.exception("crowdsec_service_alerts failed")
        return Response({"error": str(exc)}, status=500)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def crowdsec_unban(request: Request) -> Response:
    """Unban an IP or CIDR range.
    
    Body: {"ip": "1.2.3.4"|"1.2.3.0/24", "range_type": "Ip"|"Range"}
    """
    try:
        ip = (request.data.get("ip") or "").strip()
        range_type = request.data.get("range_type", "Ip")
        if not ip:
            return Response({"error": "ip is required"}, status=400)
        if range_type not in ("Ip", "Range"):
            return Response({"error": "range_type must be 'Ip' or 'Range'"}, status=400)
        
        result = get_crowdsec_service().unban(ip, range_type)
        if "error" in result:
            return Response(result, status=500)
        
        # Audit log
        from apps.core.models import AuditLog
        AuditLog(
            actor=request.user.get_username(),
            action="CROWDSEC_UNBAN",
            target=f"IP {ip}",
            metadata={"ip": ip, "range_type": range_type},
        ).save()
        
        return Response(result)
    except Exception as exc:
        logger.exception("crowdsec_unban failed")
        return Response({"error": str(exc)}, status=500)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def crowdsec_metrics(request: Request) -> Response:
    """Proxy CrowdSec Prometheus metrics."""
    try:
        metrics = get_crowdsec_service().get_metrics()
        return Response(metrics)
    except Exception as exc:
        logger.exception("crowdsec_metrics failed")
        return Response({"error": str(exc)}, status=500)


def get_service_name(service_id: str) -> Optional[str]:
    """Lookup service name by id."""
    try:
        from apps.deployments.models import Service
        svc = Service.objects.only("name").filter(id=service_id).first()
        return svc.name if svc else None
    except Exception:
        return None