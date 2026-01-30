"""
Audit Log Model for SMSLY Hosting.

Provides immutable audit logging for security-critical operations.
"""
import uuid
from django.db import models
from django.contrib.auth import get_user_model


class AuditLog(models.Model):
    """
    Immutable audit log for security and compliance.
    
    Records user actions, API calls, and system events for accountability.
    """
    class ActionType(models.TextChoices):
        # Service actions
        SERVICE_CREATE = 'SERVICE_CREATE', 'Service Created'
        SERVICE_DELETE = 'SERVICE_DELETE', 'Service Deleted'
        SERVICE_UPDATE = 'SERVICE_UPDATE', 'Service Updated'
        
        # Deployment actions
        DEPLOYMENT_START = 'DEPLOYMENT_START', 'Deployment Started'
        DEPLOYMENT_SUCCESS = 'DEPLOYMENT_SUCCESS', 'Deployment Succeeded'
        DEPLOYMENT_FAIL = 'DEPLOYMENT_FAIL', 'Deployment Failed'
        DEPLOYMENT_ROLLBACK = 'DEPLOYMENT_ROLLBACK', 'Deployment Rollback'
        
        # Environment variable actions
        ENV_VAR_CREATE = 'ENV_VAR_CREATE', 'Environment Variable Created'
        ENV_VAR_UPDATE = 'ENV_VAR_UPDATE', 'Environment Variable Updated'
        ENV_VAR_DELETE = 'ENV_VAR_DELETE', 'Environment Variable Deleted'
        
        # Addon actions
        ADDON_PROVISION = 'ADDON_PROVISION', 'Addon Provisioned'
        ADDON_DEPROVISION = 'ADDON_DEPROVISION', 'Addon Deprovisioned'
        
        # Access actions
        LOGIN = 'LOGIN', 'User Login'
        LOGOUT = 'LOGOUT', 'User Logout'
        TOKEN_REFRESH = 'TOKEN_REFRESH', 'Token Refreshed'
        
        # Admin actions
        ADMIN_IMPERSONATE = 'ADMIN_IMPERSONATE', 'Admin Impersonation'
        PERMISSION_CHANGE = 'PERMISSION_CHANGE', 'Permission Changed'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # Who
    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='audit_logs'
    )
    user_email = models.EmailField(null=True, blank=True)  # Preserved even if user deleted
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, null=True, blank=True)
    
    # What
    action_type = models.CharField(max_length=50, choices=ActionType.choices, db_index=True)
    resource_type = models.CharField(max_length=50, db_index=True)  # e.g., 'Service', 'Deployment'
    resource_id = models.CharField(max_length=64, db_index=True)  # UUID or ID of the resource
    
    # Details
    details = models.JSONField(default=dict, blank=True)  # Additional context
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'action_type']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['resource_type', 'resource_id']),
        ]
        # SECURITY: Prevent updates and deletes in application code
        # (Should also be enforced via DB triggers in production)
        permissions = [
            ('view_audit_log', 'Can view audit logs'),
        ]
    
    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {self.action_type} by {self.user_email or 'System'}"
    
    def save(self, *args, **kwargs):
        """Prevent updates to existing audit logs."""
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise ValueError("Audit logs are immutable and cannot be updated.")
        
        # Preserve user email for historical reference
        if self.user and not self.user_email:
            self.user_email = self.user.email
        
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        """Prevent deletion of audit logs."""
        raise ValueError("Audit logs are immutable and cannot be deleted.")
