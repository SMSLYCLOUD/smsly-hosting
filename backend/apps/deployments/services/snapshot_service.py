"""Lightweight snapshot service for capturing and restoring service
configuration state without the heavyweight Docker image/volume/DB
operations of ``BackupService``.

A snapshot is a metadata-only JSON record (no tarball on disk) that
captures env vars, deploy settings, domains, resources, addons, etc.
Snapshots are instant to create, zero-cost in storage, and useful for
quick config rollback, deployment diffs, and audit trails.
"""
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def _mask_env_value(key: str, value: str) -> str:
    """Mask sensitive env var values for snapshot storage.

    Secrets are stored as ``****`` to avoid leaking credentials in
    snapshot diffs and API responses.  Also masks URL-suffixed vars
    that contain embedded credentials (e.g. DATABASE_URL contains
    user:password@host).

    The masking heuristic matches the one used in
    ``BackupService.backup_service()`` metadata.
    """
    import re as _re

    sensitive_substrings = (
        'SECRET', 'PASSWORD', 'TOKEN', 'KEY', 'PRIVATE',
        'CREDENTIALS', 'AUTH', 'API_KEY',
        'DSN',
    )
    upper_key = key.upper()

    # Check for secret substrings (sorted by length desc to match
    # more specific patterns first, e.g. API_KEY before KEY)
    for s in sorted(sensitive_substrings, key=len, reverse=True):
        if s in upper_key:
            return '****'

    # URL-suffixed vars often embed credentials in the URL itself
    # (e.g. DATABASE_URL=postgres://user:pass@host/db).
    # These should be masked even when the key doesn't contain
    # the word SECRET/KEY/etc.
    if upper_key.endswith('_URL') or upper_key == 'URL':
        # Only mask if the value actually contains credentials
        # (scheme://...@... pattern)
        if _re.match(r'\w+://[^@]+@', value):
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
        from apps.deployments.models.core import EnvironmentVariable

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
            from apps.deployments.models.addons import Addon
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
        include_db: bool = False,
    ):
        """Capture the current config of a service as a ``ServiceSnapshot``.

        When ``trigger`` is ``PRE_DEPLOY`` and ``include_db`` is True (default
        for PRE_DEPLOY), also creates a PostgreSQL database clone named
        ``smsly_snap_<service_id_short>_<timestamp>`` so the deployment can be
        rolled back at the data level.

        Returns the created ``ServiceSnapshot`` instance.
        """

        from apps.deployments.models.backup import ServiceSnapshot
        from apps.deployments.models.core import Service

        service = Service.objects.get(id=service_id)
        config_data = SnapshotService.build_config_data(service)

        # For PRE_DEPLOY, include DB clone by default
        if trigger == 'PRE_DEPLOY':
            include_db = True

        # ── Database clone for rollback safety ───────────────────────
        db_clone_name = None
        if include_db:
            db_clone_name = SnapshotService._capture_db_snapshot(
                service, snapshot_id_hint=None,
            )
            if db_clone_name:
                config_data['_db_clone'] = db_clone_name

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
            "Snapshot %s created for service %s (trigger=%s%s)",
            snapshot.id, service.name, trigger,
            f", db_clone={db_clone_name}" if db_clone_name else "",
        )

        # Upload to cloud storage if scheduled with cloud destination
        from apps.deployments.models.backup import SnapshotSchedule
        sched = SnapshotSchedule.objects.filter(service=service).first()
        if sched and sched.storage_backend == 's3' and sched.s3_bucket:
            import json
            import tempfile

            from apps.deployments.services.backup_service import upload_backup_to_s3
            try:
                timestamp = snapshot.created_at.strftime('%Y%m%d_%H%M%S')
                s3_key = f"smsly-snapshots/service-{service.id}/snapshot-{timestamp}.json"
                with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                    json.dump(config_data, f, indent=2)
                    path = f.name

                upload_backup_to_s3(
                    path, sched.s3_bucket, s3_key,
                    endpoint=sched.s3_endpoint, region=sched.s3_region,
                    access_key=sched.s3_access_key, secret_key=sched.s3_secret_key,
                )
                import os
                os.unlink(path)
                snapshot.cloud_uploaded = True
                snapshot.cloud_bucket = sched.s3_bucket
                snapshot.cloud_key = s3_key
                snapshot.save(update_fields=['cloud_uploaded', 'cloud_bucket', 'cloud_key'])
                logger.info("Uploaded snapshot %s to %s/%s", snapshot.id, sched.s3_bucket, s3_key)
            except Exception as up_exc:
                # Sanitize exception to prevent credential leakage in logs
                safe_msg = str(up_exc)
                for sensitive in (sched.s3_access_key, sched.s3_secret_key, sched.s3_endpoint):
                    if sensitive and sensitive in safe_msg:
                        safe_msg = safe_msg.replace(sensitive, '[REDACTED]')
                logger.error("Failed to upload snapshot %s: %s", snapshot.id, safe_msg)

        return snapshot

    @staticmethod
    def _capture_db_snapshot(
        service, snapshot_id_hint: str | None = None,
    ) -> str | None:
        """Clone the service's database for rollback safety.

        Uses PostgresSnapshotManager to create a fast TEMPLATE-based
        clone. Returns the clone database name, or None on failure.
        """
        from urllib.parse import urlparse

        from apps.deployments.models.addons import Addon

        # Find the service's POSTGRES addon connection URL
        db_url = None
        try:
            addon = Addon.objects.filter(
                service=service, addon_type='POSTGRES', status='ACTIVE',
            ).first()
            if addon and addon.connection_url:
                db_url = addon.connection_url
        except Exception:
            pass

        if not db_url:
            logger.info(
                "No POSTGRES addon for service %s; skipping DB snapshot", service.id,
            )
            return None

        try:
            parsed = urlparse(db_url)
            source_db_name = parsed.path.lstrip('/')
            if not source_db_name:
                logger.warning("Could not extract DB name from URL for service %s", service.id)
                return None

            short_id = str(service.id).split('-')[0] if service.id else 'svc'
            hint_suffix = f"_{snapshot_id_hint}" if snapshot_id_hint else ""
            import time as _time
            ts = int(_time.time())
            clone_name = f"smsly_snap_{short_id}{hint_suffix}_{ts}"

            from .safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
            mgr = PostgresSnapshotManager(admin_db_url=db_url)
            success = mgr.create_clone(source_db_name, clone_name)
            if success:
                logger.info(
                    "DB snapshot created: %s → %s for service %s",
                    source_db_name, clone_name, service.id,
                )
                return clone_name
            else:
                logger.warning(
                    "DB snapshot failed for service %s: clone creation returned False",
                    service.id,
                )
                return None
        except Exception as exc:
            logger.warning(
                "DB snapshot failed for service %s: %s", service.id, exc,
            )
            return None

    @staticmethod
    def _restore_db_clone(service, clone_db_name: str) -> bool:
        """Restore a service's database from a previously-created DB clone.

        This drops the current database and renames the clone in its place
        (via TEMPLATE copy-back). If the rename fails, falls back to a
        pg_dump/psql pipeline.

        Returns True on success, False on failure.
        """
        from urllib.parse import urlparse

        from apps.deployments.models.addons import Addon

        db_url = None
        try:
            addon = Addon.objects.filter(
                service=service, addon_type='POSTGRES', status='ACTIVE',
            ).first()
            if addon and addon.connection_url:
                db_url = addon.connection_url
        except Exception:
            pass

        if not db_url:
            logger.warning(
                "No POSTGRES addon for service %s; cannot restore DB clone",
                service.id,
            )
            return False

        try:
            parsed = urlparse(db_url)
            current_db_name = parsed.path.lstrip('/')
            if not current_db_name:
                return False

            from .safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
            mgr = PostgresSnapshotManager(admin_db_url=db_url)

            # Kill connections to current DB, drop it, clone from snapshot
            # Use pg_dump from clone into current DB (safer than DROP/CREATE)
            try:
                # Clone from the snapshot DB back to the original
                maintenance_url = mgr._get_maintenance_url()

                # Terminate connections on current DB
                mgr._run_psql_vars(
                    maintenance_url,
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :'target_db' AND pid <> pg_backend_pid();",
                    variables={'target_db': current_db_name},
                    check=False,
                )

                # Drop current and clone from snapshot
                mgr.destroy_clone(current_db_name)
                success = mgr.create_clone(clone_db_name, current_db_name)
                if success:
                    logger.info(
                        "DB restored from clone %s → %s for service %s",
                        clone_db_name, current_db_name, service.id,
                    )
                    return True

                logger.warning(
                    "DB restore via TEMPLATE failed for %s; trying pg_dump fallback",
                    service.id,
                )
            except Exception as template_exc:
                logger.warning(
                    "DB restore via TEMPLATE failed for %s: %s",
                    service.id, template_exc,
                )

            # Fallback: pipe pg_dump from clone into psql to current DB
            clone_url = mgr.get_clone_url(clone_db_name)
            current_url = mgr._build_db_url(current_db_name)
            dump_proc = subprocess.Popen(
                ['pg_dump', '-d', clone_url, '--no-owner', '--no-acl'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            restore_proc = subprocess.Popen(
                ['psql', '-d', current_url, '-v', 'ON_ERROR_STOP=1'],
                stdin=dump_proc.stdout, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            if dump_proc.stdout:
                dump_proc.stdout.close()
            _stdout, stderr = restore_proc.communicate(timeout=600)
            dump_proc.wait(timeout=60)

            if restore_proc.returncode == 0:
                logger.info("DB restored via pg_dump for service %s", service.id)
                return True

            logger.error(
                "DB restore via pg_dump failed for %s: %s",
                service.id, stderr.strip() or '(empty)',
            )
            return False

        except Exception as exc:
            logger.error(
                "DB restore failed for service %s: %s", service.id, exc,
            )
            return False

    @staticmethod
    def cleanup_db_clone(clone_db_name: str, db_url: str | None = None) -> bool:
        """Clean up a database clone after it's no longer needed.

        Should be called after a successful deployment to remove the
        snapshot clone and free storage.
        """
        if not clone_db_name:
            return False
        try:
            from .safedeploy.postgres_snapshot_manager import PostgresSnapshotManager
            mgr = PostgresSnapshotManager(admin_db_url=db_url)
            return mgr.destroy_clone(clone_db_name)
        except Exception as exc:
            logger.warning("Failed to clean up DB clone %s: %s", clone_db_name, exc)
            return False

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

        The entire restore is wrapped in a transaction to prevent
        partial apply if env var restoration fails mid-way.

        Returns a summary dict of what was changed.
        """
        from django.db import transaction

        from apps.deployments.models.backup import ServiceSnapshot
        from apps.deployments.models.core import EnvironmentVariable, Service

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

        with transaction.atomic():
            # ── Apply scalar service fields ──────────────────────────
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

        # transaction.atomic() committed here

        # ── Restore DB clone if present ──────────────────────────────
        db_clone_restored = False
        db_clone_name = config.get('_db_clone')
        if db_clone_name:
            db_clone_restored = SnapshotService._restore_db_clone(
                target_service, db_clone_name,
            )
        result = {
            'snapshot_id': str(snapshot.id),
            'target_service_id': str(target_service.id),
            'config_changes': len(changes),
            'env_var_changes': env_changes,
            'db_clone_restored': db_clone_restored,
            'changes': changes,
            'redeployed': False,
            'requested_by': str(requesting_user.id) if requesting_user else None,
        }

        # ── Optionally trigger redeployment ──────────────────────────
        if redeploy:
            try:
                from apps.deployments.tasks import smart_deploy_task
                from apps.deployments.models import Deployment
                new_dep = Deployment.objects.create(
                    service=target_service,
                    status='QUEUED',
                    commit_hash='snapshot-restore',
                    commit_message='Redeploy after snapshot restore',
                )
                smart_deploy_task.delay(
                    deployment_id=str(new_dep.id),
                    provider_id=str(target_service.provider_id or ''),
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
        from apps.deployments.models.backup import ServiceSnapshot

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
