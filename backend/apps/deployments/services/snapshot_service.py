"""Lightweight snapshot service for capturing and restoring service
configuration state without the heavyweight Docker image/volume/DB
operations of ``BackupService``.

A snapshot is a metadata-only JSON record (no tarball on disk) that
captures env vars, deploy settings, domains, resources, addons, etc.
Snapshots are instant to create, zero-cost in storage, and useful for
quick config rollback, deployment diffs, and audit trails.
"""
import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _mask_env_value(key: str, value: str) -> str:
    """Mask sensitive env var values for snapshot storage.

    Secrets are stored as ``****`` to avoid leaking credentials in
    snapshot diffs and API responses.  The masking heuristic matches
    the one used in ``BackupService.backup_service()`` metadata.
    """
    sensitive_substrings = (
        'SECRET', 'PASSWORD', 'TOKEN', 'KEY', 'PRIVATE',
        'CREDENTIALS', 'AUTH', 'API_KEY',
    )
    upper_key = key.upper()
    for s in sensitive_substrings:
        if s in upper_key:
            return '****'
    return value


class SnapshotService:
    """Service layer for creating, restoring, and diffing config snapshots."""

    # ── Capture ──────────────────────────────────────────────────────

    @staticmethod
    def build_config_data(service) -> dict[str, Any]:
        """Assemble the config payload from a live Service instance and
        its related models (env vars, addons, custom domains, etc.).
        """
        from apps.deployments.models_core import EnvironmentVariable

        # Env vars (mask secrets)
        env_vars = {}
        try:
            for ev in EnvironmentVariable.objects.filter(service=service):
                env_vars[ev.key] = _mask_env_value(ev.key, ev.value or '')
        except Exception:
            logger.warning("Could not read env vars for service %s", service.id)

        # Addons summary
        addons = []
        try:
            from apps.deployments.models_addons import Addon
            for addon in Addon.objects.filter(service=service):
                addons.append({
                    'id': str(addon.id),
                    'name': addon.name,
                    'addon_type': addon.addon_type,
                    'status': addon.status,
                })
        except Exception:
            pass

        return {
            # Source/deploy
            'deploy_type': service.deploy_type,
            'buildpack': service.buildpack,
            'docker_image': service.docker_image or '',
            'repository_url': service.repository_url or '',
            'branch': service.branch,
            'build_command': service.build_command or '',
            'start_command': service.start_command or '',
            'root_directory': service.root_directory,

            # Network/domain
            'internal_port': service.internal_port,
            'public_domain': service.public_domain or '',
            'public_domain_hidden': service.public_domain_hidden,
            'custom_domains': service.custom_domains or [],
            'is_public': service.is_public,

            # Resources
            'cpu_cores': str(service.cpu_cores),
            'memory_mb': service.memory_mb,
            'min_replicas': service.min_replicas,
            'max_replicas': service.max_replicas,
            'autoscale_cpu_target': service.autoscale_cpu_target,
            'vpa_enabled': service.vpa_enabled,

            # Deploy strategy
            'deploy_strategy': service.deploy_strategy,
            'canary_percentage': service.canary_percentage,

            # Health
            'health_check_path': service.health_check_path,
            'health_check_port': service.health_check_port,
            'health_check_interval': service.health_check_interval,
            'health_check_timeout': service.health_check_timeout,
            'health_check_retries': service.health_check_retries,
            'auto_restart': service.auto_restart,

            # Rollback
            'auto_rollback_enabled': service.auto_rollback_enabled,
            'auto_rollback_threshold': service.auto_rollback_threshold,

            # Restart
            'restart_policy': service.restart_policy,

            # Compose
            'deploy_mode': service.deploy_mode,
            'compose_file': service.compose_file,
            'compose_main_service': service.compose_main_service,

            # SafeDeploy
            'safedeploy_enabled': service.safedeploy_enabled,
            'preview_environments_enabled': service.preview_environments_enabled,
            'migration_auto_approval_policy': service.migration_auto_approval_policy,
            'production_requires_backup': service.production_requires_backup,

            # Env vars & addons
            'env_vars': env_vars,
            'addons': addons,
        }

    @staticmethod
    def capture_snapshot(
        service_id: str,
        trigger: str = 'MANUAL',
        label: str = '',
        created_by=None,
    ):
        """Capture the current config of a service as a ``ServiceSnapshot``.

        Returns the created ``ServiceSnapshot`` instance.
        """
        from apps.deployments.models_backup import ServiceSnapshot
        from apps.deployments.models_core import Service

        service = Service.objects.get(id=service_id)
        config_data = SnapshotService.build_config_data(service)

        # Find most recent snapshot for this service (parent for diff chain)
        parent = ServiceSnapshot.objects.filter(
            service=service,
        ).order_by('-created_at').first()

        # Compute diff from parent if one exists
        diff_summary = None
        if parent and parent.config_data:
            diff_summary = SnapshotService.compute_diff(
                parent.config_data, config_data,
            )

        snapshot = ServiceSnapshot.objects.create(
            service=service,
            created_by=created_by,
            label=label,
            trigger=trigger,
            config_data=config_data,
            parent_snapshot=parent,
            diff_summary=diff_summary,
        )

        logger.info(
            "Snapshot %s created for service %s (trigger=%s)",
            snapshot.id, service.name, trigger,
        )
        return snapshot

    # ── Restore ──────────────────────────────────────────────────────

    @staticmethod
    def restore_snapshot(
        snapshot_id: str,
        target_service_id: str | None = None,
        redeploy: bool = False,
        requesting_user=None,
    ) -> dict[str, Any]:
        """Re-apply a snapshot's config to a service.

        Only config fields are restored (env vars, resources, domains,
        deploy settings).  Docker images and volume data are NOT touched.

        If ``redeploy`` is True, a new deployment is queued after the
        config is applied.

        Returns a summary dict of what was changed.
        """
        from apps.deployments.models_backup import ServiceSnapshot
        from apps.deployments.models_core import EnvironmentVariable, Service

        snapshot = ServiceSnapshot.objects.select_related('service').get(
            id=snapshot_id,
        )
        target_service = (
            Service.objects.get(id=target_service_id)
            if target_service_id
            else snapshot.service
        )

        config = snapshot.config_data
        changes = []

        # ── Apply scalar service fields ──────────────────────────────
        scalar_fields = [
            'deploy_type', 'buildpack', 'docker_image', 'repository_url',
            'branch', 'build_command', 'start_command', 'root_directory',
            'internal_port', 'public_domain', 'public_domain_hidden',
            'custom_domains', 'is_public', 'memory_mb',
            'min_replicas', 'max_replicas', 'autoscale_cpu_target',
            'vpa_enabled', 'deploy_strategy', 'canary_percentage',
            'health_check_path', 'health_check_port',
            'health_check_interval', 'health_check_timeout',
            'health_check_retries', 'auto_restart',
            'auto_rollback_enabled', 'auto_rollback_threshold',
            'restart_policy', 'deploy_mode', 'compose_file',
            'compose_main_service', 'safedeploy_enabled',
            'preview_environments_enabled',
            'migration_auto_approval_policy', 'production_requires_backup',
        ]

        update_fields = []
        for field in scalar_fields:
            if field not in config:
                continue
            old_val = getattr(target_service, field, None)
            new_val = config[field]
            # Handle cpu_cores as Decimal
            if field == 'cpu_cores':
                from decimal import Decimal
                new_val = Decimal(str(new_val))
            if old_val != new_val:
                setattr(target_service, field, new_val)
                update_fields.append(field)
                changes.append({
                    'field': field,
                    'old': str(old_val),
                    'new': str(new_val),
                })

        # cpu_cores special handling (stored as string in config)
        if 'cpu_cores' in config:
            from decimal import Decimal
            new_cpu = Decimal(config['cpu_cores'])
            if target_service.cpu_cores != new_cpu:
                target_service.cpu_cores = new_cpu
                update_fields.append('cpu_cores')
                changes.append({
                    'field': 'cpu_cores',
                    'old': str(target_service.cpu_cores),
                    'new': str(new_cpu),
                })

        if update_fields:
            target_service.save(update_fields=update_fields)

        # ── Restore env vars (non-masked only) ───────────────────────
        env_changes = 0
        snapshot_env_vars = config.get('env_vars', {})
        if snapshot_env_vars:
            for key, value in snapshot_env_vars.items():
                if value == '****':
                    continue  # Don't overwrite with masked value
                ev, created = EnvironmentVariable.objects.get_or_create(
                    service=target_service, key=key,
                    defaults={'value': value},
                )
                if not created and ev.value != value:
                    ev.value = value
                    ev.save(update_fields=['value'])
                    env_changes += 1
                elif created:
                    env_changes += 1

        result = {
            'snapshot_id': str(snapshot.id),
            'target_service_id': str(target_service.id),
            'config_changes': len(changes),
            'env_var_changes': env_changes,
            'changes': changes,
            'redeployed': False,
        }

        # ── Optionally trigger redeployment ──────────────────────────
        if redeploy:
            try:
                from apps.deployments.tasks import trigger_deployment_task
                trigger_deployment_task.delay(
                    service_id=str(target_service.id),
                )
                result['redeployed'] = True
            except Exception as exc:
                logger.warning(
                    "Failed to trigger redeploy after snapshot restore: %s",
                    exc,
                )

        logger.info(
            "Snapshot %s restored to service %s: %d config changes, "
            "%d env var changes",
            snapshot.id, target_service.name,
            len(changes), env_changes,
        )
        return result

    # ── Diff ─────────────────────────────────────────────────────────

    @staticmethod
    def compute_diff(
        old_config: dict[str, Any],
        new_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute a structured diff between two config payloads.

        Returns a dict with ``added``, ``removed``, ``changed`` keys,
        each containing the relevant field names and values.
        """
        added = {}
        removed = {}
        changed = {}

        all_keys = set(old_config.keys()) | set(new_config.keys())
        for key in sorted(all_keys):
            old_val = old_config.get(key)
            new_val = new_config.get(key)

            if key not in old_config:
                added[key] = new_val
            elif key not in new_config:
                removed[key] = old_val
            elif old_val != new_val:
                changed[key] = {'old': old_val, 'new': new_val}

        return {
            'added': added,
            'removed': removed,
            'changed': changed,
            'total_changes': len(added) + len(removed) + len(changed),
        }

    @staticmethod
    def diff_snapshots(
        snapshot_a_id: str,
        snapshot_b_id: str,
    ) -> dict[str, Any]:
        """Compute a diff between two snapshots by ID.

        Returns the snapshot metadata and the computed diff.
        """
        from apps.deployments.models_backup import ServiceSnapshot

        snap_a = ServiceSnapshot.objects.get(id=snapshot_a_id)
        snap_b = ServiceSnapshot.objects.get(id=snapshot_b_id)

        diff = SnapshotService.compute_diff(
            snap_a.config_data or {},
            snap_b.config_data or {},
        )

        return {
            'snapshot_a': {
                'id': str(snap_a.id),
                'label': snap_a.label,
                'trigger': snap_a.trigger,
                'created_at': snap_a.created_at.isoformat(),
            },
            'snapshot_b': {
                'id': str(snap_b.id),
                'label': snap_b.label,
                'trigger': snap_b.trigger,
                'created_at': snap_b.created_at.isoformat(),
            },
            'diff': diff,
        }
