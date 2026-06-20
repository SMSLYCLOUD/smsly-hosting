"""Models module."""
import uuid

from django.db import models


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    organization = models.ForeignKey(  # type: ignore[var-annotated]
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='teams', null=True, blank=True,
        help_text="The organization this team belongs to. If null, the team is personal.",
    )
    owner = models.ForeignKey(  # type: ignore[var-annotated]
        'auth.User',
        on_delete=models.CASCADE,
        related_name='owned_teams',
        null=True)
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MEMBER = 'MEMBER', 'Member'
        VIEWER = 'VIEWER', 'Viewer'

    team = models.ForeignKey(  # type: ignore[var-annotated]
        Team,
        on_delete=models.CASCADE,
        related_name='members')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)  # type: ignore[var-annotated]
    role = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER)

    class Meta:
        unique_together = ('team', 'user')
