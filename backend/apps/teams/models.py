"""Models module."""
import uuid
from django.db import models


class Team(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    organization = models.ForeignKey(
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='teams', null=True, blank=True,
        help_text="The organization this team belongs to. If null, the team is personal.",
    )
    owner = models.ForeignKey(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='owned_teams',
        null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MEMBER = 'MEMBER', 'Member'
        VIEWER = 'VIEWER', 'Viewer'

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='members')
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER)

    class Meta:
        unique_together = ('team', 'user')
