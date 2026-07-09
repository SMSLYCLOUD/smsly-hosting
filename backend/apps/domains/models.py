"""Models module."""
from apps.deployments.models import Service  # type: ignore[attr-defined]
from django.db import models


class DomainStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    DNS_PENDING = 'dns_pending', 'DNS Pending'
    DNS_VERIFIED = 'dns_verified', 'DNS Verified'
    SSL_PROVISIONING = 'ssl_provisioning', 'SSL Provisioning'
    ACTIVE = 'active', 'Active'
    SSL_FAILED = 'ssl_failed', 'SSL Failed'

class Domain(models.Model):
    domain_name = models.CharField(max_length=255, unique=True)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='domain_instances')

    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=DomainStatus.choices,
        default=DomainStatus.PENDING,
    )

    dns_expected = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    dns_actual = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    last_error = models.TextField(blank=True, null=True)  # type: ignore[var-annotated]

    verified = models.BooleanField(default=False)  # type: ignore[var-annotated]
    ssl_active = models.BooleanField(default=False)  # type: ignore[var-annotated]

    issued_at = models.DateTimeField(blank=True, null=True)  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(blank=True, null=True)  # type: ignore[var-annotated]

    checked_at = models.DateTimeField(blank=True, null=True)  # type: ignore[var-annotated]
    ssl_fail_count = models.IntegerField(default=0)  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    def __str__(self):
        return self.domain_name
