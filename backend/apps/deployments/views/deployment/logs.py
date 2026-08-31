"""logs mixin."""
from __future__ import annotations

import logging
import re as _re

from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _find_container_for_logs(deployment: object) -> tuple[object, str]:
    """Find the Docker container for a deployment using multiple strategies.

    Tries in order:
      1. deployment.container_id (direct lookup)
      2. smsly.service_id label (matches any deploy type)
      3. service.name substring match (legacy fallback)

    Returns (container, source_string) or (None, reason_string).
    """
    from apps.cloud.docker_client import get_docker_client

    client = get_docker_client()
    service = deployment.service
    service_name = service.name
    service_id = str(service.pk)
    container_id = (deployment.container_id or "").strip()

    # Strategy 1: Direct container_id lookup
    if container_id:
        try:
            container = client.containers.get(container_id)
            if container.status == 'running':
                return container, f"found by container_id={container_id}"
        except Exception as exc:
            logger.debug("Container lookup by ID %s failed: %s", container_id, exc)

        # Also try by short_id / name (container_id might be a compose name)
        try:
            containers = client.containers.list(
                filters={'name': container_id, 'status': 'running'},
            )
            if containers:
                return containers[0], f"found by container_id name match={container_id}"
        except Exception as exc:
            logger.debug("Container lookup by name %s failed: %s", container_id, exc)

    # Strategy 2: Label-based lookup (most reliable for all deploy types)
    try:
        containers = client.containers.list(
            filters={'label': f'smsly.service_id={service_id}', 'status': 'running'},
        )
        if containers:
            return containers[0], f"found by label smsly.service_id={service_id}"
    except Exception as exc:
        logger.debug("Container lookup by label service_id=%s failed: %s", service_id, exc)

    # Strategy 3: Name substring match (legacy fallback)
    try:
        containers = client.containers.list(
            filters={'name': service_name, 'status': 'running'},
        )
        if containers:
            return containers[0], f"found by name substring={service_name}"
    except Exception as exc:
        logger.debug("Container lookup by name substring %s failed: %s", service_name, exc)

    # Strategy 4: Try stopped/exited containers (for crash log viewing)
    if container_id:
        try:
            container = client.containers.get(container_id)
            return container, f"found stopped container_id={container_id}"
        except Exception as exc:
            logger.debug("Stopped container lookup by ID %s failed: %s", container_id, exc)

    return None, "no matching container found (tried container_id, label, name)"


class LogsActionsMixin:
    """LogsActions actions for the viewset."""


    @action(detail=True, methods=['get'], url_path='build-logs')
    def build_logs(self, request: object, pk: str | None = None) -> Response:
        """
        Get build logs for a deployment (REST fallback for non-WebSocket).
        GET /api/v1/deployments/{id}/build-logs/
        """
        deployment = self.get_object()
        return Response({
            'id': str(deployment.id),
            'status': deployment.status,
            'build_logs': deployment.build_logs,
            'runtime_logs': getattr(deployment, 'runtime_logs', '') or '',
            'started_at': deployment.started_at,
            'finished_at': deployment.finished_at,
            'duration_seconds': deployment.duration_seconds,
        })


    @action(detail=True, methods=['get'], url_path='runtime-logs')
    def runtime_logs(self, request: object, pk: str | None = None) -> Response:
        """
        Get live runtime logs from the deployed Docker container.
        GET /api/v1/deployments/{id}/runtime-logs/?tail=200
        """
        deployment = self.get_object()
        tail = int(request.query_params.get('tail', 200))
        tail = min(tail, 1000)  # Cap at 1000 lines

        service = deployment.service

        try:
            from apps.deployments.utils.target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target.get("server_obj")
            target_type = target.get("target_type")
        except Exception:
            active_server = getattr(service, 'server', None)
            target_type = "remote" if active_server and not active_server.is_primary else "local"

        if target_type in ("remote", "lite_agent") and active_server:
            if not deployment.remote_deployment_id:
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': 'No remote deployment ID found. The deployment may not have successfully synced to the remote node.',
                })
            try:
                from apps.deployments.services.remote_orchestrator import (
                    RemoteOrchestrator,
                )
                orchestrator = RemoteOrchestrator(active_server)
                resp = orchestrator._request(
                    method='GET',
                    path=f"/api/v1/deployments/{deployment.remote_deployment_id}/runtime-logs/",
                    params={'tail': tail},
                    timeout=15,
                )
                if resp and resp.status_code == 200:
                    data = resp.json()
                    # Re-map ID back to local deployment ID for frontend consistency
                    data['id'] = str(deployment.id)
                    return Response(data)

                err_detail = f"HTTP {resp.status_code if resp else 'None'}"
                if resp and resp.content:
                    try:
                        err_json = resp.json()
                        err_text = err_json.get("message") or err_json.get("detail") or str(err_json)
                        if err_text:
                            err_detail = f"HTTP {resp.status_code}: {err_text}"
                    except Exception as exc:
                        logger.debug("Failed to parse remote error JSON response: %s", exc)
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Failed to fetch logs from remote node: {err_detail}",
                })
            except Exception as e:
                logger.warning("Failed to proxy runtime logs to remote node: %s", e)
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Remote proxy error: {e!s}",
                })

        try:
            container, source = _find_container_for_logs(deployment)

            if not container:
                # Container is dead or removed — fallback to saved logs.
                # Prefer the dedicated runtime_logs field (crash output is
                # written there since the build/runtime separation); only
                # legacy deployments need the marker-scrape of build_logs.
                saved_runtime = getattr(deployment, 'runtime_logs', '') or ''
                if saved_runtime.strip():
                    fallback_logs = saved_runtime
                else:
                    saved_logs = deployment.build_logs or ""
                    crash_match = _re.search(
                        r"--- (?:Runtime Crash Logs|Runtime Failure Logs)[^\n]*\n(.*?)--- End (?:Crash|Failure) Logs ---",
                        saved_logs, _re.DOTALL
                    )
                    fallback_logs = (
                        crash_match.group(1).strip()
                        if crash_match
                        else (saved_logs[-4000:] if saved_logs else "")
                    )
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': fallback_logs,
                    'source': 'saved_runtime_logs',
                    'message': 'Container is not running. Showing saved runtime/crash logs.',
                })

            logs = container.logs(
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=True,
            )
            log_text = logs.decode('utf-8', errors='replace')

            return Response({
                'id': str(deployment.id),
                'container_id': container.short_id,
                'container_status': container.status,
                'runtime_logs': log_text,
                'source': 'live_container',
                'lookup': source,
            })

        except ImportError:
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': 'Docker SDK not available.',
            })
        except Exception as e:
            logger.warning("Failed to fetch runtime logs for %s: %s", pk, e)
            err_msg = str(e)
            if any(term in err_msg.lower() for term in ["nameresolutionerror", "socket-proxy", "connection", "maxretryerror", "getaddrinfo"]):
                err_msg = "Cannot connect to Docker daemon or socket-proxy. Please verify Docker is running and reachable."
            # Fallback to saved build_logs
            saved_logs = deployment.build_logs or ""
            fallback_logs = saved_logs[-4000:] if saved_logs else ""
            return Response({
                'id': str(deployment.id),
                'runtime_logs': fallback_logs,
                'source': 'build_logs',
                'message': f'Could not fetch live runtime logs: {err_msg}. Showing saved crash logs.',
            })


    @action(detail=True, methods=['post'])
    def diagnose(self, request: object, pk: str | None = None) -> Response:
        """
        Trigger AI diagnosis for a deployment.
        """
        deployment = self.get_object()
        from apps.deployments.tasks.ai.tasks_ai import analyze_failure_task

        # Trigger analysis asynchronously
        try:
            analyze_failure_task.delay(deployment_id=str(deployment.id))
        except Exception as exc:
            # Avoid hard-failing the API when the broker is unavailable.
            try:
                from kombu.exceptions import OperationalError as BrokerOperationalError
            except Exception:  # pragma: no cover
                BrokerOperationalError = ()

            if BrokerOperationalError and isinstance(exc, BrokerOperationalError):
                logger.warning(
                    "Unable to queue AI diagnosis task for deployment %s: broker unavailable",
                    deployment.id,
                )
            else:
                logger.exception(
                    "Unable to queue AI diagnosis task for deployment %s",
                    deployment.id,
                )

        return Response({'message': 'Analysis started'})
