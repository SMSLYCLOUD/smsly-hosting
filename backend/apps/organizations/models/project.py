"""Project model and memberships."""
import uuid

from django.conf import settings
from django.db import models

from apps.deployments.models.core import Project


class ProjectMember(models.Model):
    """Per-project membership with role and optional permission overrides.

    Project-level membership takes precedence over team-level membership.
    A user who is a VIEWER on the team can be promoted to ADMIN on a
    specific project without giving them team-wide admin rights.
    """

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MEMBER = 'MEMBER', 'Member'
        VIEWER = 'VIEWER', 'Viewer'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    project = models.ForeignKey(  # type: ignore[var-annotated]
        Project,
        on_delete=models.CASCADE,
        related_name='project_members',
    )
    user = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    role = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    permissions = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="Custom permission code overrides for this member at the project level. "
                  "If set, these replace the default role + team permissions.",
    )
    joined_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    expires_at = models.DateTimeField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="If set, membership automatically expires after this date",
    )

    class Meta:
        db_table = 'deployments_projectmember'
        unique_together = [('project', 'user')]
        ordering = ['-joined_at']
        verbose_name = "Project Member"
        verbose_name_plural = "Project Members"

    def __str__(self):
        return f"{self.user} ({self.role}) @ {self.project}"
