"""
Healing, diagnostics, and incident mixins for ManagedServerViewSet.
"""

import logging

from rest_framework import status
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response

from .helpers import _is_command_allowed, _redact_transfer_text
from .serializers import ServerCommandThrottle, ServerHealThrottle

logger = logging.getLogger(__name__)


class HealingMixin:

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerHealThrottle])
    def heal(self, request, pk=None):
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = request.data.get("action", "full")
        deployment_id = request.data.get("deployment_id")

        if action == "diagnose":
            return self._run_diagnostics(server)

        if deployment_id:
            try:
                from apps.deployments.models.core import Deployment
                deployment = Deployment.objects.get(id=deployment_id)
            except (Deployment.DoesNotExist, ValueError):
                return Response(
                    {"error": f"Deployment {deployment_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            from apps.deployments.tasks.deployment.tasks_deploy_remote import self_heal_remote_deployment
            self_heal_remote_deployment.delay(
                deployment_id=str(deployment.id),
                server_id=str(server.id),
            )
            return Response({
                "status": "healing_triggered",
                "deployment_id": str(deployment.id),
                "message": "Self-healing task queued",
            })

        if action in ("restart_container", "restart_docker_daemon", "restart_stack", "full"):
            return self._trigger_node_healing(server, action)

        return Response(
            {"error": f"Unknown action: {action}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=["get", "post"])
    def diagnostics(self, request, pk=None):
        server = self.get_object()
        return self._run_diagnostics(server)

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerCommandThrottle])
    def run_command(self, request, pk=None):
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        command = request.data.get("command", "").strip()
        if not command:
            return Response(
                {"error": "Command is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _is_command_allowed(command):
            return Response(
                {"error": "Command not allowed. Only safe docker subcommands (ps, logs, stats, inspect, images, info, version, df, top, port, events) and system diagnostic commands are permitted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            out, err, code = orchestrator._exec(command, timeout=60)
            orchestrator._close_ssh()

            try:
                redacted_out = _redact_transfer_text(out or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stdout: %s", exc)
                redacted_out = "[REDACTION FAILED — output suppressed for safety]"
            try:
                redacted_err = _redact_transfer_text(err or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stderr: %s", exc)
                redacted_err = "[REDACTION FAILED — output suppressed for safety]"

            return Response({
                "command": command,
                "exit_code": code,
                "stdout": redacted_out[:10000],
                "stderr": redacted_err[:5000],
            })
        except Exception as exc:
            return Response(
                {"error": f"Command execution failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['get'], url_path='incident-report')
    def incident_report(self, request, pk=None):
        from django.db.models import Q

        from apps.deployments.models.audit import AuditLog
        from apps.deployments.models.backup import ServiceBackup
        from apps.deployments.models.core import Deployment, Service
        from apps.deployments.models.transfer import ServerTransfer

        server = self.get_object()

        events: list = []
        server_name = server.name or server.host or str(server.id)

        failure_statuses = [
            'FAILED', 'CANCELLED', 'BUILD_FAILED', 'BACKUP_FAILED',
            'MIGRATION_FAILED', 'HEALTH_CHECK_FAILED',
        ]
        services = list(Service.objects.filter(server=server))
        failed_deploys = (
            Deployment.objects
            .filter(service__in=services, status__in=failure_statuses)
            .select_related('service')
            .order_by('-created_at')[:30]
        )
        for d in failed_deploys:
            events.append({
                'type': 'deployment',
                'severity': 'critical' if d.status == 'FAILED' else 'warning',
                'timestamp': d.created_at.isoformat() if d.created_at else '',
                'title': f"{d.service.name}: deployment {d.status.lower().replace('_', ' ')}",
                'detail': (d.commit_message or '')[:500],
                'service_id': str(d.service_id),
                'service_name': d.service.name,
                'deployment_id': str(d.id),
                'status': d.status,
            })

        failed_backups = (
            ServiceBackup.objects
            .filter(service__in=services, status='FAILED')
            .select_related('service')
            .order_by('-created_at')[:10]
        )
        for b in failed_backups:
            events.append({
                'type': 'backup_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': f"{b.service.name}: backup failed",
                'detail': b.error_message or '',
                'service_id': str(b.service_id),
                'backup_id': str(b.id),
            })

        health_actions = [
            'HEALTH_TRANSITION', 'SERVICE_HEALTHY', 'SERVICE_UNHEALTHY',
        ]
        service_ids = [str(s.id) for s in services]
        health_audits = []
        if service_ids:
            from django.db.models import Q as QQ
            health_filter = QQ()
            for sid in service_ids:
                health_filter |= QQ(metadata__contains={'service_id': sid})
            health_audits = list(
                AuditLog.objects
                .filter(health_filter)
                .filter(action__in=health_actions)
                .order_by('-timestamp')[:20]
            )
        for a in health_audits:
            previous = (a.metadata or {}).get('previous', '')
            current = (a.metadata or {}).get('current', '')
            events.append({
                'type': 'health',
                'severity': (
                    'critical' if a.action == 'SERVICE_UNHEALTHY' else 'warning'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': f'{previous} → {current}' if previous and current else a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        target_ip_match = Q(target_server_ip=server.host)
        if server.private_ip:
            target_ip_match |= Q(target_server_ip=server.private_ip)
        transfers = (
            ServerTransfer.objects
            .filter(
                Q(source_server_id=str(server.id)) | target_ip_match,
            )
            .exclude(status='COMPLETED')
            .order_by('-created_at')[:10]
        )
        for t in transfers:
            events.append({
                'type': 'transfer',
                'severity': 'critical' if t.status == 'FAILED' else 'warning',
                'timestamp': t.created_at.isoformat() if t.created_at else '',
                'title': f"Server transfer {t.status.lower()}",
                'detail': t.error_message or 'Source → Target',
                'transfer_id': str(t.id),
                'status': t.status,
            })

        prov_logs = getattr(server, 'provision_logs', '') or ''
        if prov_logs:
            prov_lines = prov_logs.split('\n')
            for line in reversed(prov_lines[-20:]):
                lower = line.strip().lower()
                if not lower:
                    continue
                if 'error' in lower or 'fail' in lower or 'exception' in lower:
                    events.append({
                        'type': 'provisioning',
                        'severity': 'warning',
                        'timestamp': '',
                        'title': 'Provisioning error detected',
                        'detail': line.strip()[:300],
                    })

        service_list = [
            {'id': str(s.id), 'name': s.name, 'status': s.status}
            for s in services
        ]
        active_count = sum(1 for s in services if s.status == 'ACTIVE')

        events.sort(key=lambda e: e['timestamp'] or '', reverse=True)

        severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
        for e in events:
            sev = e.get('severity', 'info')
            if sev in severity_counts:
                severity_counts[sev] += 1

        return Response({
            'server_id': str(server.id),
            'server_name': server_name,
            'server_status': server.status,
            'total_services': len(service_list),
            'active_services': active_count,
            'total_events': len(events),
            'critical': severity_counts['critical'],
            'warning': severity_counts['warning'],
            'info': severity_counts['info'],
            'services': service_list,
            'events': events,
        })

    def _run_diagnostics(self, server):
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            diagnostics = orchestrator.run_full_diagnostics()
            orchestrator._close_ssh()

            return Response({
                "server": {
                    "id": str(server.id),
                    "name": server.name,
                    "host": server.host,
                },
                "docker_running": diagnostics.docker_running,
                "disk_usage_pct": diagnostics.disk_usage_pct,
                "memory_usage_pct": diagnostics.memory_usage_pct,
                "network_reachable": diagnostics.network_reachable,
                "failure_type": diagnostics.failure_type.value,
                "container_state": diagnostics.container_state,
                "error_details": diagnostics.error_details,
                "suggested_actions": [a.value for a in diagnostics.suggested_actions],
                "exited_containers": diagnostics.raw_diagnostics.get("exited_containers", ""),
            })
        except Exception as exc:
            return Response(
                {"error": f"Diagnostics failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _trigger_node_healing(self, server, action: str):
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                RecoveryAction,
                SelfHealingOrchestrator,
            )

            orchestrator = SelfHealingOrchestrator(server)

            action_map = {
                "restart_container": RecoveryAction.RESTART_CONTAINER,
                "restart_docker_daemon": RecoveryAction.RESTART_DOCKER_DAEMON,
                "restart_stack": RecoveryAction.RESTART_STACK,
                "full": RecoveryAction.RESTART_STACK,
            }
            recovery_action = action_map.get(action, RecoveryAction.RESTART_STACK)

            class _FakeDeployment:
                id = "manual"
                container_id = ""
                service = type("obj", (object,), {"name": ""})()

            result = orchestrator._execute_recovery(
                recovery_action, _FakeDeployment(), orchestrator._diagnostics
            )
            orchestrator._close_ssh()

            return Response({
                "action": recovery_action.value,
                "success": result.success,
                "details": result.details,
                "post_recovery_status": result.post_recovery_status,
                "next_action": result.next_action.value if result.next_action else None,
                "heal_log": orchestrator.get_heal_log()[-10:],
            })
        except Exception as exc:
            return Response(
                {"error": f"Healing failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
