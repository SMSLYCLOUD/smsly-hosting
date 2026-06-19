"""Models module."""
from django.db import models
from apps.deployments.models import Service

class DomainStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    DNS_PENDING = 'dns_pending', 'DNS Pending'
    DNS_VERIFIED = 'dns_verified', 'DNS Verified'
    SSL_PROVISIONING = 'ssl_provisioning', 'SSL Provisioning'
    ACTIVE = 'active', 'Active'
    SSL_FAILED = 'ssl_failed', 'SSL Failed'

class Domain(models.Model):
    domain_name = models.CharField(max_length=255, unique=True)
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='domain_instances')

    status = models.CharField(
        max_length=20,
        choices=DomainStatus.choices,
        default=DomainStatus.PENDING,
    )

    dns_expected = models.CharField(max_length=255, blank=True, null=True)
    dns_actual = models.CharField(max_length=255, blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)

    verified = models.BooleanField(default=False)
    ssl_active = models.BooleanField(default=False)

    issued_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    checked_at = models.DateTimeField(blank=True, null=True)
    ssl_fail_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.domain_name
