"""
Regression tests for Issue 50.

A Service must always belong to a Project. The
``pre_save`` signal in ``apps/deployments/models.py`` back-fills the
owner's default project whenever a service is saved without one.
The public ``ProjectViewSet.remove_service`` action is disabled
because it would orphan services in violation of the contract.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models_project import Project as ProjectReexport


class ServiceProjectDefaultSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="svc-project", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_service_creation_assigns_default_project(self):
        self.assertFalse(
            Project.objects.filter(owner=self.user).exists(),
            "Pre-condition: no project yet",
        )
        service = Service.objects.create(
            name="orphan-svc", owner=self.user,
        )
        service.refresh_from_db()
        self.assertIsNotNone(service.project)
        self.assertTrue(service.project.is_default)
        self.assertEqual(service.project.owner, self.user)

    def test_existing_default_project_is_reused(self):
        existing = Project.objects.create(
            owner=self.user, name="my-default", is_default=True,
        )
        service = Service.objects.create(name="reuse-svc", owner=self.user)
        service.refresh_from_db()
        self.assertEqual(service.project_id, existing.id)

    def test_explicit_project_is_preserved(self):
        chosen = Project.objects.create(
            owner=self.user, name="chosen", is_default=False,
        )
        service = Service.objects.create(
            name="keep-svc", owner=self.user, project=chosen,
        )
        self.assertEqual(service.project_id, chosen.id)

    def test_reassigning_to_none_re_populates_default(self):
        chosen = Project.objects.create(
            owner=self.user, name="chosen2", is_default=False,
        )
        service = Service.objects.create(
            name="reassign-svc", owner=self.user, project=chosen,
        )
        # Simulate the old remove-service behaviour: project = None.
        service.project = None
        service.save()
        service.refresh_from_db()
        self.assertIsNotNone(service.project)
        # The signal created (or reused) a default project.
        self.assertTrue(service.project.is_default)
        self.assertEqual(service.project.owner, self.user)


class ProjectRemoveServiceEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="remove-svc", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(
            owner=self.user, name="p", is_default=False,
        )
        self.service = Service.objects.create(
            name="to-remove", owner=self.user, project=self.project,
        )

    def test_remove_service_action_rejected(self):
        url = (
            f"/api/v1/projects/{self.project.id}/remove-service/"
        )
        resp = self.client.post(
            url,
            data={"service_id": str(self.service.id)},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        # The service must still belong to a project.
        self.service.refresh_from_db()
        self.assertIsNotNone(self.service.project)
