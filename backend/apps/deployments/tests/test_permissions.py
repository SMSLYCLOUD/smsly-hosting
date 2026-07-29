# pylint: disable=invalid-name
"""Tests for service permission scoping via the ServiceViewSet.

Validates that get_queryset, assert_can_write, and assert_can_delete
correctly gate access based on:
  - Service owner (personal service)
  - Team membership role (ADMIN, MEMBER, VIEWER)
  - Superuser bypass
  - Non-member / anonymous denial
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.deployments.models import Service
from apps.teams.models import Team, TeamMember

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "perm-tests",
    }
}


def _make_user(username):
    return User.objects.create_user(
        username=username, email=f"{username}@test.com", password="testpass123"
    )


@override_settings(CACHES=TEST_CACHES)
class OwnerServiceVisibilityTests(APITestCase):
    """Tests 1-2: Owner sees own services, non-owner cannot."""

    def setUp(self):
        self.owner = _make_user("owner_perm")
        self.other = _make_user("other_perm")
        self.service = Service.objects.create(
            name="owner-svc-1",
            owner=self.owner,
            deploy_type="GIT",
        )

    def test_owner_can_list_own_service(self):
        """Owner sees their own service in the list."""
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [s.get("id") or s.get("name") for s in response.data]
        self.assertIn(str(self.service.id), [str(i) for i in ids])

    def test_owner_can_retrieve_own_service(self):
        """Owner can retrieve a specific service by PK."""
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f"/api/v1/services/{self.service.id}/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)

    def test_other_user_cannot_see_owner_service(self):
        """A different user with no team link cannot see this service."""
        self.client.force_authenticate(user=self.other)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertNotIn(str(self.service.id), ids)

    def test_other_user_cannot_retrieve_owner_service(self):
        """A different user cannot retrieve a specific owned service."""
        self.client.force_authenticate(user=self.other)
        response = self.client.get(f"/api/v1/services/{self.service.id}/")
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)


@override_settings(CACHES=TEST_CACHES)
class TeamMemberVisibilityTests(APITestCase):
    """Tests 3, 6: Team members can see team services; non-members cannot."""

    def setUp(self):
        self.owner = _make_user("team_owner")
        self.member = _make_user("team_member")
        self.viewer = _make_user("team_viewer")
        self.admin = _make_user("team_admin")
        self.non_member = _make_user("non_member")

        self.team = Team.objects.create(name="Acme", owner=self.owner)
        TeamMember.objects.create(
            team=self.team, user=self.member, role="MEMBER"
        )
        TeamMember.objects.create(
            team=self.team, user=self.viewer, role="VIEWER"
        )
        TeamMember.objects.create(
            team=self.team, user=self.admin, role="ADMIN"
        )

        from apps.deployments.models import Project

        self.project = Project.objects.create(
            name="Team Project",
            slug="team-project",
            owner=self.owner,
            team=self.team,
        )
        self.team_service = Service.objects.create(
            name="team-svc-1",
            owner=self.owner,
            project=self.project,
            deploy_type="GIT",
        )

    def test_member_can_list_team_service(self):
        """A MEMBER can see team services in the list."""
        self.client.force_authenticate(user=self.member)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertIn(str(self.team_service.id), ids)

    def test_viewer_can_list_team_service(self):
        """A VIEWER can see team services in the list (read access)."""
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertIn(str(self.team_service.id), ids)

    def test_admin_can_list_team_service(self):
        """An ADMIN can see team services in the list."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertIn(str(self.team_service.id), ids)

    def test_non_member_cannot_see_team_service(self):
        """A user with no team membership cannot see team services."""
        self.client.force_authenticate(user=self.non_member)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertNotIn(str(self.team_service.id), ids)


@override_settings(CACHES=TEST_CACHES)
class ViewerCannotModifyTests(APITestCase):
    """Test 4: VIEWER role cannot update or delete services."""

    def setUp(self):
        self.owner = _make_user("viewer_test_owner")
        self.viewer = _make_user("viewer_test_viewer")

        self.team = Team.objects.create(name="Viewer Team", owner=self.owner)
        TeamMember.objects.create(
            team=self.team, user=self.viewer, role="VIEWER"
        )

        from apps.deployments.models import Project

        self.project = Project.objects.create(
            name="Viewer Proj", slug="viewer-proj",
            owner=self.owner, team=self.team,
        )
        self.service = Service.objects.create(
            name="viewer-svc",
            owner=self.owner,
            project=self.project,
            deploy_type="GIT",
        )

    def test_viewer_cannot_update_service(self):
        """VIEWER gets 403 when trying to update a team service."""
        self.client.force_authenticate(user=self.viewer)
        response = self.client.patch(
            f"/api/v1/services/{self.service.id}/",
            {"name": "hacked-name"},
            format="json",
        )
        self.assertIn(
            response.status_code,
            (http_status.HTTP_403_FORBIDDEN, http_status.HTTP_400_BAD_REQUEST),
        )

    def test_viewer_cannot_delete_service(self):
        """VIEWER gets 403 when trying to delete a team service."""
        self.client.force_authenticate(user=self.viewer)
        response = self.client.delete(f"/api/v1/services/{self.service.id}/")
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)


@override_settings(CACHES=TEST_CACHES)
class AdminCanModifyTests(APITestCase):
    """Test 5: ADMIN role can modify team services."""

    def setUp(self):
        self.owner = _make_user("admin_test_owner")
        self.admin = _make_user("admin_test_admin")

        self.team = Team.objects.create(name="Admin Team", owner=self.owner)
        TeamMember.objects.create(
            team=self.team, user=self.admin, role="ADMIN"
        )

        from apps.deployments.models import Project

        self.project = Project.objects.create(
            name="Admin Proj", slug="admin-proj",
            owner=self.owner, team=self.team,
        )
        self.service = Service.objects.create(
            name="admin-svc",
            owner=self.owner,
            project=self.project,
            deploy_type="GIT",
        )

    def test_admin_can_update_service(self):
        """ADMIN can update a team service."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/v1/services/{self.service.id}/",
            {"name": "admin-rename"},
            format="json",
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.service.refresh_from_db()
        self.assertEqual(self.service.name, "admin-rename")

    def test_admin_can_delete_service(self):
        """ADMIN can initiate deletion of a team service."""
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/v1/services/{self.service.id}/")
        self.assertIn(
            response.status_code,
            (http_status.HTTP_202_ACCEPTED, http_status.HTTP_200_OK),
        )


@override_settings(CACHES=TEST_CACHES)
class SuperuserBypassTests(APITestCase):
    """Test 7: Superuser can see and modify all services."""

    def setUp(self):
        self.owner = _make_user("su_owner")
        self.su = User.objects.create_superuser(
            username="super_perm", email="su@test.com", password="testpass123"
        )
        self.service = Service.objects.create(
            name="su-svc",
            owner=self.owner,
            deploy_type="GIT",
        )

    def test_superuser_can_list_all_services(self):
        """Superuser sees all services regardless of ownership."""
        self.client.force_authenticate(user=self.su)
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        ids = [str(s.get("id", "")) for s in response.data]
        self.assertIn(str(self.service.id), ids)

    def test_superuser_can_retrieve_any_service(self):
        """Superuser can retrieve any service by PK."""
        self.client.force_authenticate(user=self.su)
        response = self.client.get(f"/api/v1/services/{self.service.id}/")
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)

    def test_superuser_can_update_any_service(self):
        """Superuser can update any service."""
        self.client.force_authenticate(user=self.su)
        response = self.client.patch(
            f"/api/v1/services/{self.service.id}/",
            {"name": "su-rename"},
            format="json",
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)


@override_settings(CACHES=TEST_CACHES)
class UnauthenticatedTests(APITestCase):
    """Test 8: Unauthenticated requests get 401."""

    def test_unauthenticated_list_returns_401(self):
        """Anonymous user gets 401 on service list."""
        response = self.client.get("/api/v1/services/")
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_unauthenticated_detail_returns_401(self):
        """Anonymous user gets 401 on service detail."""
        response = self.client.get(
            "/api/v1/services/00000000-0000-0000-0000-000000000000/"
        )
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)
