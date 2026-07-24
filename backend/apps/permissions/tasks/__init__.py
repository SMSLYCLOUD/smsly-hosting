"""Periodic tasks for membership expiry cleanup and permission maintenance."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    name='apps.permissions.tasks.deactivate_expired_memberships',
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def deactivate_expired_memberships(self):
    """Deactivate expired team, org, and project memberships.

    Runs daily. Deactivates team/org memberships (sets is_active=False)
    and deletes expired project memberships.
    """
    now = timezone.now()

    try:
        from apps.teams.models import TeamMember
        deactivated_teams = TeamMember.objects.filter(
            expires_at__lt=now, is_active=True,
        ).update(is_active=False)
        if deactivated_teams:
            logger.info("Deactivated %d expired team memberships", deactivated_teams)
    except Exception as e:
        logger.exception("Failed to clean up expired team memberships: %s", e)

    try:
        from apps.organizations.models import OrganizationMembership
        deactivated_orgs = OrganizationMembership.objects.filter(
            expires_at__lt=now, is_active=True,
        ).update(is_active=False)
        if deactivated_orgs:
            logger.info("Deactivated %d expired organization memberships", deactivated_orgs)
    except Exception as e:
        logger.exception("Failed to clean up expired org memberships: %s", e)

    try:
        from apps.organizations.models.project import ProjectMember
        deleted_projects, _ = ProjectMember.objects.filter(
            expires_at__lt=now,
        ).delete()
        if deleted_projects:
            logger.info("Deleted %d expired project memberships", deleted_projects)
    except Exception as e:
        logger.exception("Failed to clean up expired project memberships: %s", e)

    return {
        'deactivated_team_members': deactivated_teams if 'deactivated_teams' in dir() else 0,
        'deactivated_org_members': deactivated_orgs if 'deactivated_orgs' in dir() else 0,
        'deleted_project_members': deleted_projects if 'deleted_projects' in dir() else 0,
    }
