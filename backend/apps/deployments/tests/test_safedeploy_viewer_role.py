"""
Regression tests for Issue 67.

A team VIEWER must not be able to create preview environments.
The new ``_user_can_create_previews`` helper enforces this role
check explicitly. The previous ``_user_owns_or_member`` only
verified that the user had *some* role on the team, which let
VIEWERs trigger expensive builds.
"""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status as http_status
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Project, Service
from apps.deployments.models.safedeploy import PreviewEnvironment
from apps.teams.models import Team, TeamMember

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "viewer-role-tests",
    }
}


def _make_service(owner, provider):
    return Service.objects.create(
        name=f"svc-{uuid.uuid4().hex[:6]}",
        owner=owner,
        provider=provider,
        repository_url="https://github.com/test/app",
        branch="main",
    )


@override_settings(CACHES=TEST_CACHES)
class ViewerCannotCreatePreviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner-viewer", password="p",
        )
        self.viewer = User.objects.create_user(
            username="viewer-user", password="p",
        )
        self.member = User.objects.create_user(
            username="member-user", password="p",
        )
        self.admin = User.objects.create_user(
            username="admin-user", password="p",
        )
        self.provider = CloudProvider.objects.create(
            name="p", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True,
        )
        self.team = Team.objects.create(name="t", owner=self.owner)
        self.project = Project.objects.create(
            name="p", owner=self.owner, team=self.team,
        )
        self.service = _make_service(self.owner, self.provider)
        self.service.project = self.project
        self.service.preview_environments_enabled = True
        self.service.save(update_fields=["project", "preview_environments_enabled"])

        self.url = f"/api/v1/services/{self.service.id}/previews/"

    def _create_as(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        # The view imports the task lazily inside the method:
        # ``from apps.deployments.tasks_safedeploy import create_preview_environment_job``
        # so the patch must target the module where the name actually lives.
        with patch(
            "apps.deployments.tasks.deployment.tasks_safedeploy.create_preview_environment_job.delay"
        ):
            return client.post(
                self.url,
                data={"branch_name": "feat/v", "commit_sha": "a" * 7},
                format="json",
            )

    def test_viewer_blocked_with_403(self):
        TeamMember.objects.create(
            team=self.team, user=self.viewer, role=TeamMember.Role.VIEWER,
        )
        resp = self._create_as(self.viewer)
        self.assertEqual(resp.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            PreviewEnvironment.objects.filter(service=self.service).exists()
        )

    def test_member_allowed_with_201(self):
        TeamMember.objects.create(
            team=self.team, user=self.member, role=TeamMember.Role.MEMBER,
        )
        resp = self._create_as(self.member)
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

    def test_admin_allowed_with_201(self):
        TeamMember.objects.create(
            team=self.team, user=self.admin, role=TeamMember.Role.ADMIN,
        )
        resp = self._create_as(self.admin)
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)

    def test_owner_allowed_with_201(self):
        resp = self._create_as(self.owner)
        self.assertEqual(resp.status_code, http_status.HTTP_201_CREATED)
