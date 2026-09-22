"""Project-membership inheritance for services.

Adding a service to a project (at creation, via PATCH, or through the
``move-project`` action) must give it the project's properties — its
mTLS trust domain + sidecar posture above all. Network, registry chain
and shared-addon URLs already resolve from ``service.project`` at
deploy time; identity does not (ecosystem configure fns only run
inside ecosystem deploys, and MtlsConfig defaults to off /
``platform.local``).

Everything here is best-effort and never raises: membership must not
fail because inheritance did. All imports are function-level so signals
and views can use this without import cycles.
"""
import logging

logger = logging.getLogger(__name__)


def project_flavor(project) -> str:
    """Return ``'ecosystem'`` or ``'platform'`` for a project (or None).

    A project is ecosystem-flavored when an EcosystemPlan points at it
    or when it already hosts ECOSYSTEM-managed services. Anything else
    (including no project) is platform flavor.
    """
    if project is None:
        return "platform"
    try:
        from apps.deployments.models.ecosystem import EcosystemPlan
        if EcosystemPlan.objects.filter(project=project).exists():
            return "ecosystem"
    except Exception as exc:
        logger.debug("EcosystemPlan flavor check skipped: %s", exc)
    try:
        from apps.deployments.models import Service
        if Service.objects.filter(
            project=project, managed_by="ECOSYSTEM",
        ).exists():
            return "ecosystem"
    except Exception as exc:
        logger.debug("Sibling flavor check skipped: %s", exc)
    return "platform"


def apply_project_membership(service, project=None) -> dict:
    """Normalize a service to its project's identity properties.

    Sets the mTLS trust domain to the project flavor and matches the
    sidecar/enabled posture of the project's existing services (default
    on when no siblings exist yet — the plan default). Returns a
    summary dict; never raises. Takes effect on next deploy (sidecars
    are injected at deploy time).
    """
    try:
        if project is None:
            project = getattr(service, "project", None)
        flavor = project_flavor(project)
        trust_domain = (
            "ecosystem.local" if flavor == "ecosystem" else "platform.local"
        )
        enabled = True
        try:
            from apps.mtls.models import MtlsConfig
            siblings = MtlsConfig.objects.filter(
                service__project=project,
            ).exclude(service_id=getattr(service, "id", None))
            if siblings.exists():
                enabled = siblings.filter(sidecar_enabled=True).exists()
        except Exception as exc:
            logger.debug("Sibling posture check skipped: %s", exc)
        if flavor == "ecosystem":
            from apps.deployments.tasks.ecosystem.tasks import (
                _configure_ecosystem_mtls,
            )
            _configure_ecosystem_mtls(service, enabled)
        else:
            from apps.deployments.tasks.ecosystem.tasks import (
                _configure_platform_mtls,
            )
            _configure_platform_mtls(service, enabled)
        return {
            "flavor": flavor,
            "trust_domain": trust_domain,
            "mtls_enabled": enabled,
            "sidecar_enabled": enabled,
        }
    except Exception as exc:
        logger.warning(
            "Project membership inheritance skipped for service %s: %s",
            getattr(service, "name", "?"), exc,
        )
        return {"flavor": None, "error": str(exc)[:200]}


def move_service_to_project(service, target, actor_username="system") -> dict:
    """Move a service into another project with full inheritance.

    Shared by the service-side ``move-project`` action and the
    project-side ``move-service`` / ``remove-service`` actions so every
    move path behaves identically: FK move, addon project relink, mTLS
    inheritance, audit. Callers own access checks. Raises on validation
    errors; never raises from inheritance or audit. Redeploy applies.
    """
    from apps.deployments.models.addons import Addon
    from apps.deployments.models.audit import AuditLog

    if target is None:
        raise ValueError(
            "A target project is required — services must always belong "
            "to a project. Pass the default project's id to ungroup."
        )
    old_project = getattr(service, "project", None)
    if old_project is not None and str(target.id) == str(old_project.id):
        return {
            "status": "no_change",
            "from_project": str(old_project.id),
            "to_project": str(target.id),
        }
    service.project = target
    service.save(update_fields=["project", "updated_at"])
    try:
        Addon.objects.filter(service=service).exclude(
            status=Addon.Status.DELETED).update(project=target)
    except Exception as exc:
        logger.debug("Addon project relink skipped for %s: %s",
                     getattr(service, "name", "?"), exc)
    membership = apply_project_membership(service, target)
    try:
        AuditLog(
            actor=actor_username,
            action="SERVICE_MOVE_PROJECT",
            target=f"Service: {getattr(service, 'name', '?')}",
            metadata={
                "service_id": str(getattr(service, "id", "")),
                "from_project": str(old_project.id) if old_project else None,
                "to_project": str(target.id),
                "mtls": membership,
            },
        ).save()
    except Exception as exc:
        logger.debug("Move audit log skipped: %s", exc)
    return {
        "status": "ok",
        "from_project": str(old_project.id) if old_project else None,
        "to_project": str(target.id),
        "mtls": membership,
    }
