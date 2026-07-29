"""Models for the permissions app."""
import uuid

from django.conf import settings
from django.db import models


class PermissionDeniedAudit(models.Model):
    """Immutable audit record of a 403 PermissionDenied response.

    Created by PermissionAuditMiddleware for every 403 returned to an
    authenticated user so security teams can detect probing, misconfigured
    policies, and insider threats.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='permission_denials',
    )
    path = models.CharField(max_length=500)  # type: ignore[var-annotated]
    method = models.CharField(max_length=10)  # type: ignore[var-annotated]
    permission_code = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        default='unknown',
        help_text="Permission code that was denied",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)  # type: ignore[var-annotated]
    user_agent = models.TextField(blank=True, default='')  # type: ignore[var-annotated]
    timestamp = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    resource_id = models.UUIDField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="UUID of the resource being accessed, if available",
    )

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['permission_code', '-timestamp']),
        ]
        verbose_name = 'Permission Denial'
        verbose_name_plural = 'Permission Denials'

    def __str__(self):
        return f"{self.user} denied {self.permission_code} on {self.path} [{self.method}]"
