from django.contrib.auth.models import User
from django.test import TestCase

from apps.teams.models import Team, TeamMember


class Finding157TeamOwnerSetNullTests(TestCase):
    def test_owner_fk_is_set_null_with_signal_reassignment(self):
        from django.db.models.deletion import SET_NULL
        field = Team._meta.get_field('owner')
        self.assertIs(field.remote_field.on_delete, SET_NULL)
        self.assertTrue(field.null)

    def test_signal_reassigns_owner_to_remaining_admin(self):
        owner = User.objects.create_user(username='fix157owner', password='x')
        co_admin = User.objects.create_user(username='fix157admin', password='x')
        team = Team.objects.create(name='fix157-team', owner=owner)
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.ADMIN)
        TeamMember.objects.create(team=team, user=co_admin, role=TeamMember.Role.ADMIN)

        owner.delete()
        team.refresh_from_db()

        self.assertEqual(team.owner_id, co_admin.id)
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())

    def test_signal_promotes_member_when_no_remaining_admin(self):
        owner = User.objects.create_user(username='fix157owner2', password='x')
        member = User.objects.create_user(username='fix157member', password='x')
        team = Team.objects.create(name='fix157-team2', owner=owner)
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.ADMIN)
        tm_member = TeamMember.objects.create(
            team=team, user=member, role=TeamMember.Role.MEMBER,
        )

        owner.delete()

        team.refresh_from_db()
        tm_member.refresh_from_db()
        self.assertEqual(team.owner_id, member.id)
        self.assertEqual(tm_member.role, TeamMember.Role.ADMIN)

    def test_signal_clears_owner_when_no_remaining_members(self):
        owner = User.objects.create_user(username='fix157solo', password='x')
        team = Team.objects.create(name='fix157-solo', owner=owner)
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.ADMIN)

        owner.delete()
        team.refresh_from_db()
        self.assertIsNone(team.owner_id)
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())

    def test_team_survives_owner_deletion(self):
        owner = User.objects.create_user(username='fix157keep', password='x')
        co_admin = User.objects.create_user(username='fix157keepadm', password='x')
        team = Team.objects.create(name='fix157-keep', owner=owner)
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.ADMIN)
        TeamMember.objects.create(team=team, user=co_admin, role=TeamMember.Role.ADMIN)
        team_id = team.id

        owner.delete()

        self.assertTrue(Team.objects.filter(id=team_id).exists())
