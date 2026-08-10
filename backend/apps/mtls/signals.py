import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.deployments.models import Service
from apps.mtls.models import MtlsConfig

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Service)
def create_mtls_config(sender, instance, created, **kwargs):
    """Auto-create MtlsConfig when a new Service is created."""
    if created:
        try:
            config, created = MtlsConfig.objects.get_or_create(
                service=instance,
                defaults={
                    "enabled": True,
                    "trust_domain": "ecosystem.local",
                },
            )
            if created:
                logger.info("Auto-created MtlsConfig for service %s", instance.name)
        except Exception as exc:
            logger.error(
                "Failed to auto-create MtlsConfig for service %s: %s",
                instance.name, exc,
            )