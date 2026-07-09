"""Celery tasks for bundle lifecycle management.

Mirrors the task structure in ``tasks_addons.py`` so that bundle
operations are async, retryable, and observable.
"""
import logging
import time as _time

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def provision_bundle_task(
    self,
    bundle_id: str,
    build_dir: str | None = None,
):
    """Provision all components in a bundle.

    Creates BundleComponent records, spins up the Docker Compose stack,
    and injects connection URLs as env vars on the parent service.
    """
    from services.bundle_provisioner import bundle_provisioner
    from services.grid_addons_parser import load_grid_addons

    from apps.deployments.models import EnvironmentVariable
    from apps.deployments.models_bundles import Bundle, BundleComponent

    start_ts = _time.monotonic()

    try:
        bundle = Bundle.objects.get(id=bundle_id)
        service = bundle.service

        # Load the manifest from the service's source directory
        # (The build pipeline stores it in a known location)
        source_dir = build_dir or f"/tmp/smsly-deploy-{service.id}"
        manifest = load_grid_addons(source_dir)
        if manifest is None:
            logger.error("grid.addons not found for bundle %s", bundle_id)
            bundle.status = Bundle.Status.FAILED
            bundle.deletion_error = "grid.addons manifest not found"
            bundle.save(update_fields=['status', 'deletion_error'])
            return

        # Find the matching bundle declaration
        bundle_decl = None
        for b in manifest.bundles:
            if b.name == bundle.name:
                bundle_decl = b
                break
        if bundle_decl is None:
            logger.error("Bundle '%s' not found in manifest", bundle.name)
            bundle.status = Bundle.Status.FAILED
            bundle.deletion_error = f"Bundle '{bundle.name}' not found in grid.addons"
            bundle.save(update_fields=['status', 'deletion_error'])
            return

        # Collect standard addon URLs for template resolution
        addon_urls = {}
        for addon in service.addons.filter(status='ACTIVE'):
            if addon.connection_url:
                addon_urls[addon.name] = addon.connection_url
                addon_urls[addon.addon_type.lower()] = addon.connection_url

        # Provision the bundle
        component_urls = bundle_provisioner.provision(
            bundle=bundle_decl,
            service_id=str(service.id),
            service_name=service.name,
            addon_urls=addon_urls,
            build_dir=build_dir,
        )

        # Update bundle status
        bundle.status = Bundle.Status.ACTIVE
        bundle.network = bundle_provisioner._network_name(bundle_decl, str(service.id))
        bundle.save(update_fields=['status', 'network'])

        # Create/update BundleComponent records
        for svc_decl in bundle_decl.services:
            url = component_urls.get(svc_decl.name, "")
            container_name = bundle_provisioner._container_name(
                bundle.name, str(service.id), svc_decl.name,
            )
            component, _ = BundleComponent.objects.update_or_create(
                bundle=bundle,
                name=svc_decl.name,
                defaults={
                    'source_type': (
                        BundleComponent.SourceType.REPO if svc_decl.repo
                        else BundleComponent.SourceType.IMAGE
                    ),
                    'image': svc_decl.image or '',
                    'repo': svc_decl.repo or '',
                    'branch': svc_decl.branch or 'main',
                    'build_type': svc_decl.build or '',
                    'status': BundleComponent.Status.ACTIVE,
                    'container_name': container_name,
                    'connection_url': url,
                    'ports': svc_decl.ports,
                },
            )

            # Inject component env vars into parent service
            creds = component.parsed_credentials
            for key, value in creds.items():
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=key,
                    defaults={
                        'value': value,
                        'is_secret': key.endswith('_PASSWORD') or key.endswith('_URL'),
                        'source': 'ADDON',
                    },
                )

        # Report metrics
        try:
            from config.metrics import ADDON_PROVISION_DURATION
            ADDON_PROVISION_DURATION.labels(
                addon_type=f"BUNDLE_{bundle.name}",
            ).observe(_time.monotonic() - start_ts)
        except Exception:
            pass

        logger.info(
            "Bundle %s provisioned for service %s (%d components, %.1fs)",
            bundle.name, service.name, len(component_urls),
            _time.monotonic() - start_ts,
        )

    except Exception as exc:
        logger.error("Bundle provisioning failed for %s: %s", bundle_id, exc)
        try:
            bundle = Bundle.objects.get(id=bundle_id)
            if self.request.retries >= self.max_retries:
                bundle.status = Bundle.Status.FAILED
                bundle.deletion_error = str(exc)[:500]
                bundle.save(update_fields=['status', 'deletion_error'])
                return
        except Bundle.DoesNotExist:
            return
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def reprovision_bundle_task(
    self,
    bundle_id: str,
    build_dir: str | None = None,
):
    """Tear down and re-provision a bundle."""
    from services.bundle_provisioner import bundle_provisioner
    from services.grid_addons_parser import load_grid_addons

    from apps.deployments.models_bundles import Bundle

    try:
        bundle = Bundle.objects.get(id=bundle_id)
        service = bundle.service

        bundle.status = Bundle.Status.PROVISIONING
        bundle.save(update_fields=['status'])

        source_dir = build_dir or f"/tmp/smsly-deploy-{service.id}"
        manifest = load_grid_addons(source_dir)
        if manifest is None:
            raise ValueError("grid.addons not found")

        bundle_decl = None
        for b in manifest.bundles:
            if b.name == bundle.name:
                bundle_decl = b
                break
        if bundle_decl is None:
            raise ValueError(f"Bundle '{bundle.name}' not found in manifest")

        addon_urls = {}
        for addon in service.addons.filter(status='ACTIVE'):
            if addon.connection_url:
                addon_urls[addon.name] = addon.connection_url
                addon_urls[addon.addon_type.lower()] = addon.connection_url

        component_urls = bundle_provisioner.reprovision(
            bundle=bundle_decl,
            service_id=str(service.id),
            service_name=service.name,
            addon_urls=addon_urls,
            build_dir=build_dir,
        )

        bundle.status = Bundle.Status.ACTIVE
        bundle.network = bundle_provisioner._network_name(bundle_decl, str(service.id))
        bundle.save(update_fields=['status', 'network'])

        # Update BundleComponent records with new values
        from urllib.parse import urlparse as _urlparse

        from apps.deployments.models_bundles import BundleComponent
        for svc_decl in bundle_decl.services:
            url = component_urls.get(svc_decl.name, "")
            container_name = bundle_provisioner._container_name(
                bundle.name, str(service.id), svc_decl.name,
            )
            BundleComponent.objects.update_or_create(
                bundle=bundle,
                name=svc_decl.name,
                defaults={
                    'source_type': (
                        BundleComponent.SourceType.REPO
                        if svc_decl.repo
                        else BundleComponent.SourceType.IMAGE
                    ),
                    'image': svc_decl.image or '',
                    'repo': svc_decl.repo or '',
                    'branch': svc_decl.branch or 'main',
                    'build_type': svc_decl.build or '',
                    'status': BundleComponent.Status.ACTIVE,
                    'container_name': container_name,
                    'connection_url': url,
                    'ports': svc_decl.ports,
                },
            )
            if url:
                slug = svc_decl.name.upper().replace('-', '_')
                from apps.deployments.models import EnvironmentVariable
                parsed_url = _urlparse(url)
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=f"{slug}_URL",
                    defaults={'value': url, 'is_secret': True, 'source': 'ADDON'},
                )
                EnvironmentVariable.objects.update_or_create(
                    service=service,
                    key=f"{slug}_HOST",
                    defaults={
                        'value': parsed_url.hostname or url,
                        'is_secret': False,
                        'source': 'ADDON',
                    },
                )

        logger.info("Bundle %s reprovisioned for service %s", bundle.name, service.name)

    except Exception as exc:
        logger.error("Bundle reprovision failed for %s: %s", bundle_id, exc)
        try:
            bundle = Bundle.objects.get(id=bundle_id)
            if self.request.retries >= self.max_retries:
                bundle.status = Bundle.Status.FAILED
                bundle.deletion_error = str(exc)[:500]
                bundle.save(update_fields=['status', 'deletion_error'])
                return
        except Bundle.DoesNotExist:
            return
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def deprovision_bundle_task(
    self,
    bundle_id: str,
    bundle_name: str = '',
    service_id: str = '',
    network_name: str = '',
):
    """Stop and remove all containers, networks, and compose files for a bundle.

    Accepts fallback parameters (bundle_name, service_id, network_name) in
    case the Bundle row has already been deleted (async pre_delete race).
    """
    from services.bundle_provisioner import bundle_provisioner

    try:
        from apps.deployments.models_bundles import Bundle
        try:
            bundle = Bundle.objects.get(id=bundle_id)
            b_name = bundle.name
            s_id = str(bundle.service_id)
            net = bundle.network or None
        except Bundle.DoesNotExist:
            # Row already deleted — use the values passed from the signal handler
            if not bundle_name or not service_id:
                logger.error(
                    "Bundle %s row deleted and no fallback params provided",
                    bundle_id,
                )
                return
            b_name = bundle_name
            s_id = service_id
            net = network_name or None

        bundle_provisioner.deprovision(b_name, s_id, network_name=net)

        # Try to update status (may fail if row is deleted)
        try:
            bundle = Bundle.objects.get(id=bundle_id)
            bundle.status = Bundle.Status.DELETED
            bundle.save(update_fields=['status'])
        except Bundle.DoesNotExist:
            pass

        # Update component statuses
        try:
            from apps.deployments.models_bundles import BundleComponent
            BundleComponent.objects.filter(
                bundle_id=bundle_id,
            ).update(status=BundleComponent.Status.STOPPED)
        except Exception:
            pass

        logger.info("Bundle %s deprovisioned (service %s)", b_name, s_id)
    except Exception as exc:
        logger.error("Bundle deprovision failed for %s: %s", bundle_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def backup_bundle_component_task(
    self,
    component_id: str,
):
    """Create a backup for a bundle component."""
    from services.bundle_provisioner import bundle_provisioner

    from apps.deployments.models_bundles import BundleBackup, BundleComponent

    try:
        component = BundleComponent.objects.get(id=component_id)
        bundle = component.bundle

        backup = None
        if self.request.retries == 0:
            backup = BundleBackup.objects.create(
                component=component, status=BundleBackup.Status.PENDING,
            )
        else:
            backup = BundleBackup.objects.filter(
                component=component, status=BundleBackup.Status.PENDING,
            ).order_by('-created_at').first()
            if not backup:
                backup = BundleBackup.objects.create(
                    component=component, status=BundleBackup.Status.PENDING,
                )

        path = bundle_provisioner.backup(
            bundle.name, str(bundle.service.id), component.name,
        )

        import os

        from django.utils import timezone
        backup.file_path = path
        backup.size_bytes = os.path.getsize(path) if os.path.isfile(path) else 0
        backup.status = BundleBackup.Status.COMPLETED
        backup.completed_at = timezone.now()
        backup.save(update_fields=['file_path', 'size_bytes', 'status', 'completed_at'])

        logger.info("Backup completed for bundle component %s", component_id)

    except Exception as exc:
        logger.error("Bundle component backup failed for %s: %s", component_id, exc)
        if self.request.retries >= self.max_retries:
            try:
                component = BundleComponent.objects.get(id=component_id)
                backup = BundleBackup.objects.filter(
                    component=component, status=BundleBackup.Status.PENDING,
                ).order_by('-created_at').first()
                if backup:
                    backup.status = BundleBackup.Status.FAILED
                    backup.error_message = str(exc)[:500]
                    backup.save()
            except Exception:
                pass
            return
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=3)
def restore_bundle_component_task(self, backup_id: str):
    """Restore a backup to a bundle component."""
    from services.bundle_provisioner import bundle_provisioner

    from apps.deployments.models_bundles import BundleBackup

    try:
        backup = BundleBackup.objects.select_related(
            'component__bundle__service',
        ).get(id=backup_id)
        component = backup.component
        if component is None:
            raise ValueError(
                f"Backup {backup_id} has no associated component (deleted?)"
            )
        bundle = component.bundle

        bundle_provisioner.restore(
            bundle.name, str(bundle.service.id), component.name, backup.file_path,
        )
    except Exception as exc:
        logger.error("Bundle component restore failed for %s: %s", backup_id, exc)
        try:
            backup = BundleBackup.objects.get(id=backup_id)
            backup.status = BundleBackup.Status.FAILED
            backup.error_message = str(exc)[:500]
            backup.save(update_fields=['status', 'error_message'])
        except Exception:
            pass
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)


@shared_task
def delete_bundle_task(bundle_id: str):
    """Full deletion of a bundle: deprovision + remove DB records."""
    from services.bundle_provisioner import bundle_provisioner

    from apps.deployments.models_bundles import Bundle

    try:
        bundle = Bundle.objects.get(id=bundle_id)
        service = bundle.service

        success = bundle_provisioner.deprovision(
            bundle.name, str(service.id), network_name=bundle.network or None,
        )

        if success:
            bundle.delete()
            logger.info("Bundle %s deleted for service %s", bundle.name, service.name)
        else:
            bundle.status = Bundle.Status.DELETION_FAILED
            bundle.deletion_error = "Failed to remove bundle infrastructure"
            bundle.save(update_fields=['status', 'deletion_error'])

    except Exception as exc:
        logger.error("Bundle deletion failed for %s: %s", bundle_id, exc)
        try:
            bundle = Bundle.objects.get(id=bundle_id)
            bundle.status = Bundle.Status.DELETION_FAILED
            bundle.deletion_error = str(exc)[:500]
            bundle.save(update_fields=['status', 'deletion_error'])
        except Bundle.DoesNotExist:
            pass
