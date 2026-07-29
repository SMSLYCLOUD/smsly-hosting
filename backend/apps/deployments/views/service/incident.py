"""incident mixin."""
import logging

from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)



class IncidentMixin:
    """Incident actions for the viewset."""


    @action(detail=True, methods=['get'], url_path='incident-report')
    def incident_report(self, request, pk=None):
        """Aggregate incident timeline from all relevant data sources.

        GET /api/v1/services/{id}/incident-report/

        Returns a consolidated timeline covering deployments, health
        transitions, resource alerts, auto-rollbacks, backup operations,
        AI remediation, scaling events, transfer failures, snapshots,
        routing failures, mesh/network incidents, cloud upload failures,
        and CrowdSec WAF bans.
        """
        service = self.get_object()
        from apps.deployments.models.audit import AuditLog
        from apps.deployments.models.backup import ServiceBackup, ServiceSnapshot
        from apps.deployments.models.transfer import ServerTransfer
        from apps.notifications.models import ResourceAlert

        events: list = []

        # ── 1. All non-success deployments (last 90 days) ─────────────
        from apps.deployments.models.core import Deployment
        failure_statuses = [
            'FAILED', 'CANCELLED', 'BUILD_FAILED', 'BACKUP_FAILED',
            'MIGRATION_FAILED', 'HEALTH_CHECK_FAILED', 'ROLLED_BACK',
        ]
        deploys = (
            Deployment.objects
            .filter(service=service)
            .order_by('-created_at')
        )
        for d in deploys[:30]:
            is_failure = d.status in failure_statuses
            events.append({
                'type': 'deployment',
                'severity': 'critical' if d.status == 'FAILED' else (
                    'warning' if is_failure else 'info'
                ),
                'timestamp': d.created_at.isoformat() if d.created_at else '',
                'title': f"Deployment {d.status.lower().replace('_', ' ')}",
                'detail': (d.commit_message or '')[:500],
                'deployment_id': str(d.id),
                'status': d.status,
                'branch': d.branch or '',
                'is_rollback': getattr(d, 'is_rollback', False),
            })

        # ── 2. Resource alerts ───────────────────────────────────────
        alerts = (
            ResourceAlert.objects
            .filter(service=service)
            .order_by('-created_at')[:20]
        )
        for a in alerts:
            events.append({
                'type': 'resource_alert',
                'severity': a.severity.lower(),
                'timestamp': a.created_at.isoformat() if a.created_at else '',
                'title': a.title or a.metric or 'Resource alert',
                'detail': a.message or '',
                'metric': a.metric or '',
                'threshold': getattr(a, 'threshold', None),
                'current_value': getattr(a, 'current_value', None),
                'acknowledged': a.acknowledged,
                'alert_id': str(a.id),
            })

        # ── 3. Health transitions (audit log) ─────────────────────────
        health_actions = [
            'HEALTH_TRANSITION', 'SERVICE_HEALTHY', 'SERVICE_UNHEALTHY',
            'HEALTH_WEBHOOK_APPLIED', 'HEALTH_WEBHOOK_REJECTED',
        ]
        health_audits = (
            AuditLog.objects
            .filter(
                action__in=health_actions,
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:15]
        )
        for a in health_audits:
            previous = (a.metadata or {}).get('previous', '')
            current = (a.metadata or {}).get('current', '')
            title = a.metadata.get('message', '') or (
                f'{previous} → {current}' if previous and current else a.action
            )
            events.append({
                'type': 'health',
                'severity': (
                    'critical' if a.action == 'SERVICE_UNHEALTHY' else
                    'warning' if a.action == 'HEALTH_TRANSITION' else
                    'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': title,
                'detail': a.metadata.get('detail', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 4. Auto-rollback events ───────────────────────────────────
        rollback_audits = (
            AuditLog.objects
            .filter(
                action__in=['AUTO_ROLLBACK_TRIGGERED', 'STUCK_ROLLBACK_DETECTED', 'DEPLOYMENT_ROLLBACK', 'DEPLOYMENT_ROLLBACK_INSTANT'],
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:10]
        )
        for a in rollback_audits:
            events.append({
                'type': 'rollback',
                'severity': 'critical' if 'STUCK' in a.action else 'warning',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 5. Backup operations (audit log + backup model) ───────────
        backup_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'BACKUP_CREATE', 'BACKUP_RESTORE', 'BACKUP_INTEGRITY_CHECK',
                    'BACKUP_KEY_IMPORTED', 'BACKUP_CLOUD_UPLOAD_FAILED',
                ],
                target__icontains=service.name,
            )
            .order_by('-timestamp')[:10]
        )
        for a in backup_audits:
            events.append({
                'type': 'backup',
                'severity': 'warning' if 'FAILED' in a.action else 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # Also check ServiceBackup failures
        failed_backups = (
            ServiceBackup.objects
            .filter(service=service, status='FAILED')
            .order_by('-created_at')[:5]
        )
        for b in failed_backups:
            events.append({
                'type': 'backup_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': 'Backup failed',
                'detail': b.error_message or '',
                'backup_id': str(b.id),
                'backup_type': b.backup_type,
            })

        # ── 6. AI remediation events ──────────────────────────────────
        ai_audits = (
            AuditLog.objects
            .filter(
                action__in=['SCALE_UP', 'DIAGNOSE', 'DIAGNOSIS', 'CLEANUP', 'TRIGGER_JULES_FIX'],
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:10]
        )
        for a in ai_audits:
            events.append({
                'type': 'ai_remediation',
                'severity': (
                    'warning' if a.action == 'SCALE_UP' else 'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 7. Service lifecycle events ───────────────────────────────
        lifecycle_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'SERVICE_STOP', 'SERVICE_RESTART', 'SERVICE_FAST_RESTART',
                    'SERVICE_CREATE', 'SERVICE_DELETE_REQUESTED',
                ],
                target__icontains=service.name,
            )
            .order_by('-timestamp')[:10]
        )
        for a in lifecycle_audits:
            events.append({
                'type': 'service_lifecycle',
                'severity': 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 8. Server transfers ───────────────────────────────────────
        transfers = (
            ServerTransfer.objects
            .filter(service=service)
            .exclude(status='COMPLETED')
            .order_by('-created_at')[:5]
        )
        for t in transfers:
            events.append({
                'type': 'transfer',
                'severity': 'critical' if t.status == 'FAILED' else 'warning',
                'timestamp': t.created_at.isoformat() if t.created_at else '',
                'title': f"Server transfer {t.status.lower()}",
                'detail': t.error_message or '',
                'transfer_id': str(t.id),
                'status': t.status,
            })

        # ── 9. Snapshots ──────────────────────────────────────────────
        snapshots = (
            ServiceSnapshot.objects
            .filter(service=service)
            .order_by('-created_at')[:10]
        )
        for s in snapshots:
            events.append({
                'type': 'snapshot',
                'severity': 'info',
                'timestamp': s.created_at.isoformat() if s.created_at else '',
                'title': s.label or f'Snapshot {s.id}',
                'detail': f'Trigger: {s.trigger}',
                'snapshot_id': str(s.id),
                'trigger': s.trigger,
            })

        # ── 10. Routing / infrastructure events ──────────────────────
        infra_audits = (
            AuditLog.objects
            .filter(
                action__in=['CADDY_RELOAD'],
            )
            .order_by('-timestamp')[:5]
        )
        for a in infra_audits:
            events.append({
                'type': 'infrastructure',
                'severity': 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': 'Caddy reload',
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
            })

        # ── 11. Mesh / WireGuard network events ──────────────────────
        mesh_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'MESH_PEER_UNREACHABLE', 'MESH_DEPLOY_FAILED',
                    'MESH_DEPLOY_SUCCESS',
                ],
            )
            .order_by('-timestamp')[:5]
        )
        for a in mesh_audits:
            events.append({
                'type': 'mesh',
                'severity': (
                    'critical' if 'UNREACHABLE' in a.action or 'FAILED' in a.action
                    else 'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 12. Cloud / object storage upload failures ────────────────
        cloud_failures = (
            ServiceBackup.objects
            .filter(
                service=service,
                metadata__has_key='cloud_upload_error',
            )
            .order_by('-created_at')[:5]
        )
        for b in cloud_failures:
            events.append({
                'type': 'cloud_upload_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': 'Cloud backup upload failed',
                'detail': (b.metadata or {}).get('cloud_upload_error', ''),
                'backup_id': str(b.id),
            })

        # ── 13. CrowdSec WAF summary ──────────────────────────────────
        try:
            import subprocess
            bans_result = subprocess.run(
                ['docker', 'exec', 'smsly-crowdsec',
                 'cscli', 'decisions', 'list', '-o', 'json'],
                capture_output=True, text=True, timeout=10,
            )
            if bans_result.returncode == 0:
                ban_count = 0
                try:
                    import json
                    bans = json.loads(bans_result.stdout)
                    ban_count = len(bans) if isinstance(bans, list) else 0
                except (ValueError, TypeError) as exc:
                    logger.debug("Failed to parse CrowdSec ban JSON: %s", exc)
                events.append({
                    'type': 'waf_summary',
                    'severity': 'warning' if ban_count > 50 else 'info',
                    'timestamp': '',
                    'title': f'{ban_count} active WAF bans',
                    'detail': 'CrowdSec decisions currently enforcing',
                })
        except Exception as exc:
            logger.debug("CrowdSec WAF summary unavailable: %s", exc)
        events.sort(key=lambda e: e['timestamp'] or '', reverse=True)

        # Summary counts
        severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
        for e in events:
            sev = e.get('severity', 'info')
            if sev in severity_counts:
                severity_counts[sev] += 1

        return Response({
            'service_id': str(service.id),
            'service_name': service.name,
            'total_events': len(events),
            'critical': severity_counts['critical'],
            'warning': severity_counts['warning'],
            'info': severity_counts['info'],
            'events': events,
        })

    # ── Preview Environments ─────────────────────────────────────────────
