from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase

from apps.deployments.models import Service, Deployment, Project
from apps.teams.models import Team, TeamMember


User = get_user_model()


def _ownership_predicate(user, deployment_id):
    deleted_states = {
        Service.Status.DELETED,
        Service.Status.DELETION_PENDING,
        Service.Status.DELETION_FAILED,
    }
    return Deployment.objects.filter(
        Q(service__owner=user) |
        Q(service__project__team__members__user=user),
        id=deployment_id,
    ).exclude(service__status__in=deleted_states).exists()


class Finding141TeamMemberOwnershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='fix141-own', password='x')
        self.member = User.objects.create_user(username='fix141-mem', password='x')
        self.outsider = User.objects.create_user(username='fix141-out', password='x')

        self.team = Team.objects.create(name='fix141-team', owner=self.owner)
        TeamMember.objects.create(
            team=self.team, user=self.owner, role=TeamMember.Role.ADMIN,
        )
        TeamMember.objects.create(
            team=self.team, user=self.member, role=TeamMember.Role.MEMBER,
        )

        self.project = Project.objects.create(
            name='fix141-proj', owner=self.owner, team=self.team,
        )
        self.service = Service.objects.create(
            name='fix141-svc', owner=self.owner, project=self.project,
        )
        Service.objects.filter(pk=self.service.pk).update(
            status=Service.Status.ACTIVE,
        )
        self.deployment = Deployment.objects.create(
            service=self.service, commit_hash='deadbeef',
        )

    def test_service_owner_can_access(self):
        self.assertTrue(_ownership_predicate(self.owner, self.deployment.id))

    def test_team_member_can_access(self):
        self.assertTrue(_ownership_predicate(self.member, self.deployment.id))

    def test_outsider_cannot_access(self):
        self.assertFalse(_ownership_predicate(self.outsider, self.deployment.id))

    def test_removed_team_member_loses_access(self):
        self.assertTrue(_ownership_predicate(self.member, self.deployment.id))
        TeamMember.objects.filter(team=self.team, user=self.member).delete()
        self.assertFalse(_ownership_predicate(self.member, self.deployment.id))

    def test_consumer_source_contains_team_membership_check(self):
        import inspect
        from apps.deployments.consumers import TerminalConsumer
        source = inspect.getsource(TerminalConsumer._verify_ownership)
        self.assertIn('team__members__user', source)
        self.assertIn('service__owner', source)
