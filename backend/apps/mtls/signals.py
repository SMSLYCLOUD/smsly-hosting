import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.deployments.models import Service
from apps.mtls.models import MtlsConfig

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Service)
def create_mtls_config(sender, instance, created, **kwargs):
    """Auto-create MtlsConfig when a new Service is created.

    mTLS is OFF by default for user services — operators opt in per
    service (dashboard toggle / enable endpoint) or via ecosystem
    deploy, which normalizes its own rows explicitly right after
    creation. The only exception is platform-operated services
    (managed_by="PLATFORM"), which keep platform mesh enabled so the
    control plane never loses its own mTLS silently. Existing rows are
    never touched (get_or_create): flipping a default must not rewrite
    anyone's live mesh membership.
    """
    if created:
        try:
            platform_owned = (
                str(getattr(instance, "managed_by", "") or "").upper()
                == "PLATFORM"
            )
            config, created = MtlsConfig.objects.get_or_create(
                service=instance,
                defaults={
                    "enabled": platform_owned,
                    # Control-plane mesh for control-plane services;
                    # everything else belongs to the ecosystem mesh.
                    # (2026-10-11: the old blanket platform.local default
                    # stranded user workloads in the control-plane domain
                    # whenever the enable endpoint kept the preset.)
                    "trust_domain": (
                        "platform.local" if platform_owned
                        else "ecosystem.local"
                    ),
                    "sidecar_enabled": platform_owned,
                },
            )
            if created:
                logger.info(
                    "Auto-created MtlsConfig for service %s (enabled=%s)",
                    instance.name, platform_owned,
                )
        except Exception as exc:
            logger.error(
                "Failed to auto-create MtlsConfig for service %s: %s",
                instance.name, exc,
            )
