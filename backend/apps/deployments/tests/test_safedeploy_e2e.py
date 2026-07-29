import uuid
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import override_settings
from rest_framework import status as http_status
from rest_framework.test import APIRequestFactory, APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Project, Service
from apps.deployments.models.safedeploy import (
    DeploymentApproval,
    MigrationValidation,
    PreviewEnvironment,
)
from apps.teams.models import Team, TeamMember

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "safedeploy-e2e-tests",
    }
}


def _make_service(owner, provider, **kwargs):
    defaults = {
        "name": f"svc-{uuid.uuid4().hex[:6]}",
        "repository_url": "https://github.com/test/app",
        "branch": "main",
        "owner": owner,
        "provider": provider,
    }
    defaults.update(kwargs)
    return Service.objects.create(**defaults)


@override_settings(CACHES=TEST_CACHES)
class PreviewAuthTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pvowner", password="p")
        self.other = User.objects.create_user(username="pvother", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)

    def _previews_url(self, svc=None):
        svc = svc or self.service
        return f"/api/v1/services/{svc.id}/previews/"

    def test_unauthenticated_create_blocked(self):
        self.client.force_authenticate(user=None)
        r = self.client.post(self._previews_url(), {"branch_name": "feat/x", "commit_sha": "a" * 7}, format="json")
        self.assertIn(r.status_code, [http_status.HTTP_401_UNAUTHORIZED, http_status.HTTP_403_FORBIDDEN])

    def test_unauthenticated_list_blocked(self):
        self.client.force_authenticate(user=None)
        r = self.client.get(self._previews_url())
        self.assertIn(r.status_code, [http_status.HTTP_401_UNAUTHORIZED, http_status.HTTP_403_FORBIDDEN])

    def test_owner_can_create_preview(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._previews_url(),
            {"branch_name": "feat/test", "commit_sha": "a" * 7},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED)
        self.assertIn("branch_name", r.data)


@override_settings(CACHES=TEST_CACHES)
class PreviewOwnershipTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="own1", password="p")
        self.intruder = User.objects.create_user(username="intruder", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.owner, self.provider)

    def _url(self, svc=None):
        svc = svc or self.service
        return f"/api/v1/services/{svc.id}/previews/"

    def test_non_owner_cannot_create_preview(self):
        self.client.force_authenticate(user=self.intruder)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/x", "commit_sha": "a" * 7},
            format="json",
        )
        self.assertIn(r.status_code, [http_status.HTTP_403_FORBIDDEN, http_status.HTTP_404_NOT_FOUND])

    def test_non_owner_cannot_list_previews(self):
        self.client.force_authenticate(user=self.owner)
        self.client.post(self._url(), {"branch_name": "feat/x", "commit_sha": "a" * 7}, format="json")

        self.client.force_authenticate(user=self.intruder)
        r = self.client.get(self._url())
        self.assertIn(r.status_code, [http_status.HTTP_403_FORBIDDEN, http_status.HTTP_404_NOT_FOUND])

    def test_team_member_can_create_preview(self):
        team = Team.objects.create(name="review-team", owner=self.owner)
        self.service.project = Project.objects.create(name="prj", owner=self.owner, team=team)
        self.service.save(update_fields=["project"])
        TeamMember.objects.create(team=team, user=self.intruder, role=TeamMember.Role.MEMBER)

        self.client.force_authenticate(user=self.intruder)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/y", "commit_sha": "b" * 7},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED)


@override_settings(CACHES=TEST_CACHES)
class PreviewFeatureFlagTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ffuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)

    def _url(self):
        return f"/api/v1/services/{self.service.id}/previews/"

    def test_blocked_when_preview_environments_disabled(self):
        self.service.preview_environments_enabled = False
        self.service.save(update_fields=["preview_environments_enabled"])
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/x", "commit_sha": "a" * 7},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_403_FORBIDDEN)
        self.assertIn("not enabled", r.data["error"])

    def test_allowed_when_preview_environments_enabled(self):
        self.service.preview_environments_enabled = True
        self.service.save(update_fields=["preview_environments_enabled"])
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/x", "commit_sha": "a" * 7},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_201_CREATED)


@override_settings(CACHES=TEST_CACHES)
class PreviewValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="valuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)

    def _url(self):
        return f"/api/v1/services/{self.service.id}/previews/"

    def test_missing_branch_name_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self._url(), {"commit_sha": "a" * 7}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("branch_name", r.data)

    def test_missing_commit_sha_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(self._url(), {"branch_name": "feat/x"}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("commit_sha", r.data)

    def test_commit_sha_too_short_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/x", "commit_sha": "abc"},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_commit_sha_non_hex_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._url(),
            {"branch_name": "feat/x", "commit_sha": "zzzzzzz"},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_branch_name_with_invalid_chars_rejected(self):
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            self._url(),
            {"branch_name": "rm -rf /", "commit_sha": "a" * 7},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)


@override_settings(CACHES=TEST_CACHES)
class PreviewConcurrencyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ccuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)
        self.client.force_authenticate(user=self.user)

    def _rebuild_url(self, preview):
        return f"/api/v1/services/{self.service.id}/previews/{preview.id}/rebuild/"

    def _destroy_url(self, preview):
        return f"/api/v1/services/{self.service.id}/previews/{preview.id}/destroy_preview/"

    def test_rebuild_blocked_while_destroying(self):
        pv = PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="feat/x",
            commit_sha="a" * 7,
            status=PreviewEnvironment.Status.DESTROYING,
        )
        r = self.client.post(self._rebuild_url(pv), {"commit_sha": "b" * 7}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_409_CONFLICT)
        self.assertIn("destroyed", r.data["error"].lower())

    def test_destroy_blocked_while_building(self):
        pv = PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="feat/x",
            commit_sha="a" * 7,
            status=PreviewEnvironment.Status.BUILDING,
        )
        r = self.client.post(self._destroy_url(pv), format="json")
        self.assertEqual(r.status_code, http_status.HTTP_409_CONFLICT)
        self.assertIn("building", r.data["error"])

    def test_destroy_allowed_when_ready(self):
        pv = PreviewEnvironment.objects.create(
            service=self.service,
            branch_name="feat/x",
            commit_sha="a" * 7,
            status=PreviewEnvironment.Status.READY,
        )
        r = self.client.post(self._destroy_url(pv), format="json")
        self.assertEqual(r.status_code, http_status.HTTP_202_ACCEPTED)
        self.assertEqual(r.data["status"], "destroying")


@override_settings(CACHES=TEST_CACHES)
class ApprovalAuthTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="approvaluser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)

    def _approvals_url(self, svc=None):
        svc = svc or self.service
        return f"/api/v1/services/{svc.id}/approvals/"

    def test_unauthenticated_list_blocked(self):
        self.client.force_authenticate(user=None)
        r = self.client.get(self._approvals_url())
        self.assertIn(r.status_code, [http_status.HTTP_401_UNAUTHORIZED, http_status.HTTP_403_FORBIDDEN])

    def test_unauthenticated_create_blocked(self):
        self.client.force_authenticate(user=None)
        r = self.client.post(self._approvals_url(), {}, format="json")
        self.assertIn(r.status_code, [http_status.HTTP_401_UNAUTHORIZED, http_status.HTTP_403_FORBIDDEN])


@override_settings(CACHES=TEST_CACHES)
class ApprovalScopingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="scowner", password="p")
        self.intruder = User.objects.create_user(username="scintruder", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.svc_a = _make_service(self.owner, self.provider, name="svc-a")
        self.svc_b = _make_service(self.owner, self.provider, name="svc-b")

    def test_approval_list_only_returns_own_service(self):
        dep = Deployment.objects.create(
            service=self.svc_a,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        DeploymentApproval.objects.create(service=self.svc_a, deployment=dep)

        self.client.force_authenticate(user=self.owner)
        url_a = f"/api/v1/services/{self.svc_a.id}/approvals/"
        url_b = f"/api/v1/services/{self.svc_b.id}/approvals/"

        r_a = self.client.get(url_a)
        self.assertEqual(r_a.status_code, http_status.HTTP_200_OK)
        results_a = r_a.data if isinstance(r_a.data, list) else r_a.data.get("results", [])
        self.assertGreaterEqual(len(results_a), 1)

        r_b = self.client.get(url_b)
        self.assertEqual(r_b.status_code, http_status.HTTP_200_OK)
        results_b = r_b.data if isinstance(r_b.data, list) else r_b.data.get("results", [])
        self.assertEqual(len(results_b), 0)

    def test_approve_blocked_across_services(self):
        dep = Deployment.objects.create(
            service=self.svc_a,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.svc_a, deployment=dep)

        self.client.force_authenticate(user=self.owner)
        wrong_url = f"/api/v1/services/{self.svc_b.id}/approvals/{approval.id}/approve/"
        r = self.client.post(wrong_url, format="json")
        self.assertIn(r.status_code, [http_status.HTTP_403_FORBIDDEN, http_status.HTTP_404_NOT_FOUND])

    def test_approve_succeeds_for_correct_service(self):
        dep = Deployment.objects.create(
            service=self.svc_a,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.svc_a, deployment=dep)

        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/services/{self.svc_a.id}/approvals/{approval.id}/approve/"
        r = self.client.post(url, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        self.assertEqual(r.data["status"], "approved")


@override_settings(CACHES=TEST_CACHES)
class ApprovalStatusGateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="gateuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)
        self.client.force_authenticate(user=self.user)

    def _approve_url(self, approval):
        return f"/api/v1/services/{self.service.id}/approvals/{approval.id}/approve/"

    def _reject_url(self, approval):
        return f"/api/v1/services/{self.service.id}/approvals/{approval.id}/reject/"

    def test_approve_blocked_when_not_awaiting_approval(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.QUEUED,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(self._approve_url(approval), format="json")
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("not awaiting approval", r.data["error"])

    def test_reject_blocked_when_not_awaiting_approval(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.ACTIVE,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(self._reject_url(approval), {"notes": "nope"}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("not awaiting approval", r.data["error"])

    def test_approve_succeeds_when_awaiting_approval(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(self._approve_url(approval), format="json")
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)

    def test_reject_succeeds_when_awaiting_approval(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(self._reject_url(approval), {"notes": "not needed"}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)
        self.assertEqual(r.data["status"], "rejected")


@override_settings(CACHES=TEST_CACHES)
class ApprovalRejectNotesValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="notesuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)
        self.client.force_authenticate(user=self.user)

    def _reject_url(self, approval):
        return f"/api/v1/services/{self.service.id}/approvals/{approval.id}/reject/"

    def test_reject_notes_too_long_rejected(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(
            self._reject_url(approval),
            {"notes": "x" * 2001},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn("notes", r.data)

    def test_reject_notes_empty_allowed(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(self._reject_url(approval), format="json")
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)

    def test_reject_notes_within_limit_allowed(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        r = self.client.post(
            self._reject_url(approval),
            {"notes": "Valid reason"},
            format="json",
        )
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)


@override_settings(CACHES=TEST_CACHES)
class ApprovalRejectedByTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rejbyuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)
        self.client.force_authenticate(user=self.user)

    def test_rejected_by_populated_on_rejection(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        url = f"/api/v1/services/{self.service.id}/approvals/{approval.id}/reject/"
        r = self.client.post(url, {"notes": "bad deploy"}, format="json")
        self.assertEqual(r.status_code, http_status.HTTP_200_OK)

        approval.refresh_from_db()
        self.assertEqual(approval.rejected_by_id, self.user.id)
        self.assertEqual(approval.status, DeploymentApproval.Status.REJECTED)
        self.assertIsNotNone(approval.rejected_at)
        self.assertEqual(approval.approval_notes, "bad deploy")


@override_settings(CACHES=TEST_CACHES)
class SerializerValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="serialuser", password="p")

    def test_preview_create_valid(self):
        from apps.deployments.serializers import PreviewCreateSerializer
        s = PreviewCreateSerializer(data={"branch_name": "feat/x", "commit_sha": "a" * 7})
        self.assertTrue(s.is_valid())

    def test_preview_create_branch_blank(self):
        from apps.deployments.serializers import PreviewCreateSerializer
        s = PreviewCreateSerializer(data={"branch_name": "   ", "commit_sha": "a" * 7})
        self.assertFalse(s.is_valid())

    def test_preview_create_sha_non_hex(self):
        from apps.deployments.serializers import PreviewCreateSerializer
        s = PreviewCreateSerializer(data={"branch_name": "feat/x", "commit_sha": "g" * 7})
        self.assertFalse(s.is_valid())

    def test_preview_rebuild_optional_sha(self):
        from apps.deployments.serializers import PreviewRebuildSerializer
        s = PreviewRebuildSerializer(data={})
        self.assertTrue(s.is_valid())

    def test_preview_rebuild_valid_sha(self):
        from apps.deployments.serializers import PreviewRebuildSerializer
        s = PreviewRebuildSerializer(data={"commit_sha": "abcdef1"})
        self.assertTrue(s.is_valid())

    def test_preview_rebuild_invalid_sha(self):
        from apps.deployments.serializers import PreviewRebuildSerializer
        s = PreviewRebuildSerializer(data={"commit_sha": "xnopqrs"})
        self.assertFalse(s.is_valid())

    def test_reject_notes_too_long(self):
        from apps.deployments.serializers import ApprovalRejectSerializer
        s = ApprovalRejectSerializer(data={"notes": "x" * 2001})
        self.assertFalse(s.is_valid())

    def test_reject_notes_valid(self):
        from apps.deployments.serializers import ApprovalRejectSerializer
        s = ApprovalRejectSerializer(data={"notes": "fine"})
        self.assertTrue(s.is_valid())


@override_settings(CACHES=TEST_CACHES)
class CanManagePreviewsPermissionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="permowsvc", password="p")
        self.member = User.objects.create_user(username="permmember", password="p")
        self.stranger = User.objects.create_user(username="permstranger", password="p")
        self.superuser = User.objects.create_superuser(username="permsu", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.owner, self.provider)

    def _make_request(self, user):
        factory = APIRequestFactory()
        request = factory.get("/")
        request.user = user
        return request

    def test_owner_has_permission(self):
        from apps.deployments.permissions import CanManagePreviews
        perm = CanManagePreviews()
        request = self._make_request(self.owner)
        view = MagicMock(kwargs={"service_pk": str(self.service.id)})
        self.assertTrue(perm.has_permission(request, view))

    def test_stranger_denied(self):
        from apps.deployments.permissions import CanManagePreviews
        perm = CanManagePreviews()
        request = self._make_request(self.stranger)
        view = MagicMock(kwargs={"service_pk": str(self.service.id)})
        self.assertFalse(perm.has_permission(request, view))

    def test_superuser_has_permission(self):
        from apps.deployments.permissions import CanManagePreviews
        perm = CanManagePreviews()
        request = self._make_request(self.superuser)
        view = MagicMock(kwargs={"service_pk": str(self.service.id)})
        self.assertTrue(perm.has_permission(request, view))

    def test_team_member_has_permission(self):
        team = Team.objects.create(name="perm-team", owner=self.owner)
        self.service.project = Project.objects.create(name="prj2", owner=self.owner, team=team)
        self.service.save(update_fields=["project"])
        TeamMember.objects.create(team=team, user=self.member, role=TeamMember.Role.MEMBER)

        from apps.deployments.permissions import CanManagePreviews
        perm = CanManagePreviews()
        request = self._make_request(self.member)
        view = MagicMock(kwargs={"service_pk": str(self.service.id)})
        self.assertTrue(perm.has_permission(request, view))

    def test_unauthenticated_denied(self):
        from apps.deployments.permissions import CanManagePreviews
        perm = CanManagePreviews()
        request = self._make_request(AnonymousUser())
        view = MagicMock(kwargs={"service_pk": str(self.service.id)})
        self.assertFalse(perm.has_permission(request, view))


@override_settings(CACHES=TEST_CACHES)
class CanApproveDeploymentPermissionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="apppermown", password="p")
        self.stranger = User.objects.create_user(username="apppermstr", password="p")
        self.superuser = User.objects.create_superuser(username="apppermsu", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.owner, self.provider)

    def _make_request(self, user):
        factory = APIRequestFactory()
        request = factory.get("/")
        request.user = user
        return request

    def test_stranger_denied_for_approval(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)

        from apps.deployments.permissions import CanApproveDeployment
        perm = CanApproveDeployment()
        request = self._make_request(self.stranger)
        view = MagicMock(kwargs={"pk": str(approval.id), "service_pk": str(self.service.id)})
        self.assertFalse(perm.has_permission(request, view))

    def test_owner_allowed_for_low_risk(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        MigrationValidation.objects.create(
            deployment=dep,
            risk_level=MigrationValidation.RiskLevel.LOW,
            status=MigrationValidation.Status.PASSED,
        )

        from apps.deployments.permissions import CanApproveDeployment
        perm = CanApproveDeployment()
        request = self._make_request(self.owner)
        view = MagicMock(kwargs={"pk": str(approval.id), "service_pk": str(self.service.id)})
        self.assertTrue(perm.has_permission(request, view))

    def test_owner_denied_for_critical_risk(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        MigrationValidation.objects.create(
            deployment=dep,
            risk_level=MigrationValidation.RiskLevel.CRITICAL,
            status=MigrationValidation.Status.PASSED,
        )

        from apps.deployments.permissions import CanApproveDeployment
        perm = CanApproveDeployment()
        request = self._make_request(self.owner)
        view = MagicMock(kwargs={"pk": str(approval.id), "service_pk": str(self.service.id)})
        self.assertFalse(perm.has_permission(request, view))

    def test_superuser_allowed_for_critical_risk(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=self.service, deployment=dep)
        MigrationValidation.objects.create(
            deployment=dep,
            risk_level=MigrationValidation.RiskLevel.CRITICAL,
            status=MigrationValidation.Status.PASSED,
        )

        from apps.deployments.permissions import CanApproveDeployment
        perm = CanApproveDeployment()
        request = self._make_request(self.superuser)
        view = MagicMock(kwargs={"pk": str(approval.id), "service_pk": str(self.service.id)})
        self.assertTrue(perm.has_permission(request, view))

    def test_cross_service_approval_blocked(self):
        other_svc = _make_service(self.owner, self.provider, name="other-svc")
        dep = Deployment.objects.create(
            service=other_svc,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        approval = DeploymentApproval.objects.create(service=other_svc, deployment=dep)

        from apps.deployments.permissions import CanApproveDeployment
        perm = CanApproveDeployment()
        request = self._make_request(self.owner)
        view = MagicMock(kwargs={"pk": str(approval.id), "service_pk": str(self.service.id)})
        self.assertFalse(perm.has_permission(request, view))


@override_settings(CACHES=TEST_CACHES)
class PipelineTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pipeuser", password="p")
        self.provider = CloudProvider.objects.create(
            name="test-provider", provider_type="LOCAL", is_active=True
        )
        self.service = _make_service(self.user, self.provider)

    def test_reject_sets_rejected_by(self):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.reject_deployment(dep, self.user, notes="nope")

        self.assertEqual(approval.rejected_by_id, self.user.id)
        self.assertEqual(approval.status, DeploymentApproval.Status.REJECTED)
        dep.refresh_from_db()
        self.assertEqual(dep.status, Deployment.Status.CANCELLED)

    def test_reject_rolls_back_on_error(self):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        pipeline = ProductionDeploymentPipeline()
        with patch.object(Deployment, 'save', side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                pipeline.reject_deployment(dep, self.user, notes="should roll back")

        self.assertFalse(
            DeploymentApproval.objects.filter(deployment=dep, status=DeploymentApproval.Status.REJECTED).exists()
        )

    def test_approve_sets_approved_by(self):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="a" * 7,
            status=Deployment.Status.AWAITING_APPROVAL,
        )
        pipeline = ProductionDeploymentPipeline()
        approval = pipeline.approve_deployment(dep, self.user)

        self.assertEqual(approval.approved_by_id, self.user.id)
        self.assertEqual(approval.status, DeploymentApproval.Status.APPROVED)
        self.assertIsNotNone(approval.approved_at)

    def test_get_latest_validation_finds_deployment_validation(self):
        from apps.deployments.services.safedeploy.deployment_pipeline import (
            ProductionDeploymentPipeline,
        )

        dep = Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234",
            status=Deployment.Status.QUEUED,
        )
        MigrationValidation.objects.create(
            deployment=dep,
            risk_level=MigrationValidation.RiskLevel.LOW,
            status=MigrationValidation.Status.PASSED,
        )

        pipeline = ProductionDeploymentPipeline()
        val = pipeline._get_latest_validation_for_commit(self.service.id, "abc1234")
        self.assertIsNotNone(val)
        self.assertEqual(val.risk_level, MigrationValidation.RiskLevel.LOW)
