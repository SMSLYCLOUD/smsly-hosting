import logging

from django.conf import settings
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import Team, TeamMember

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def reassign_team_ownership_before_user_delete(sender, instance, **kwargs):
    try:
        owned_teams = Team.objects.filter(owner_id=instance.pk)
        for team in owned_teams:
            remaining_admin = (
                TeamMember.objects
                .filter(team=team, role=TeamMember.Role.ADMIN)
                .exclude(user_id=instance.pk)
                .select_related('user')
                .order_by('id')
                .first()
            )

            if remaining_admin is not None:
                team.owner = remaining_admin.user
                team.save(update_fields=['owner'])
                logger.info(
                    "Team %s ownership reassigned from user %s to %s before deletion",
                    team.id, instance.pk, remaining_admin.user_id,
                )
                continue

            remaining_member = (
                TeamMember.objects
                .filter(team=team)
                .exclude(user_id=instance.pk)
                .select_related('user')
                .order_by('id')
                .first()
            )
            if remaining_member is not None:
                remaining_member.role = TeamMember.Role.ADMIN
                remaining_member.save(update_fields=['role'])
                team.owner = remaining_member.user
                team.save(update_fields=['owner'])
                logger.info(
                    "Team %s had no remaining admin; promoted user %s to ADMIN and "
                    "reassigned ownership from user %s before deletion",
                    team.id, remaining_member.user_id, instance.pk,
                )
                continue

            team.owner = None
            team.save(update_fields=['owner'])
            logger.warning(
                "Team %s has no remaining members after user %s deletion; "
                "owner cleared to NULL (team now orphaned)",
                team.id, instance.pk,
            )
    except Exception as exc:
        logger.error(
            "Failed to reassign team ownership before user %s deletion: %s",
            instance.pk, exc,
        )
