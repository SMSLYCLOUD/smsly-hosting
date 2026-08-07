# pylint: disable=invalid-name
"""Regression tests for Finding #50 (ProjectViewSet.remove_service orphan).

Before the fix, removing a service from a project would have left the
service with ``project = NULL``. License tier checks and billing assume
``service.project`` is non-null, so the service would silently fall off
billing rails. The fix requires a ``replacement_project_id`` in the
request body and re-attaches the service to that project in the same
``transaction.atomic`` block.

These tests verify:
  * Missing/blank ``replacement_project_id`` -> 400, no DB change.
  * Invalid ``replacement_project_id`` (caller has no access) -> 404,
    no DB change.
  * Valid request -> service is moved to the replacement project in
    the same transaction.
"""


from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service

User = get_user_model()


class Finding50NoServiceOrphanTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fix50-user', password='x', email='fix50@example.com',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.project = Project.objects.create(
            owner=self.user, name='orig',
        )
        self.replacement = Project.objects.create(
            owner=self.user, name='replacement',
        )
        self.service = Service.objects.create(
            name='fix50-svc', owner=self.user, project=self.project,
        )

    def _url(self, project_id):
        return f'/api/v1/projects/{project_id}/remove-service/'

    def test_remove_without_replacement_project_is_rejected(self):
        """No ``replacement_project_id`` in body -> 400, service stays put."""
        resp = self.client.post(
            self._url(self.project.id),
            data={'service_id': str(self.service.id)},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.service.refresh_from_db()
        self.assertEqual(self.service.project_id, self.project.id)

    def test_remove_with_unknown_replacement_is_rejected(self):
        """A replacement project the caller cannot access -> 404, no change."""
        other_user = User.objects.create_user(
            username='fix50-other', password='x',
        )
        other_project = Project.objects.create(owner=other_user, name='other')

        resp = self.client.post(
            self._url(self.project.id),
            data={
                'service_id': str(self.service.id),
                'replacement_project_id': str(other_project.id),
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 404)
        self.service.refresh_from_db()
        self.assertEqual(self.service.project_id, self.project.id)

    def test_remove_with_replacement_moves_service_atomically(self):
        """Valid replacement -> service is attached to the new project."""
        resp = self.client.post(
            self._url(self.project.id),
            data={
                'service_id': str(self.service.id),
                'replacement_project_id': str(self.replacement.id),
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['replacement_project_id'], str(self.replacement.id))

        self.service.refresh_from_db()
        self.assertEqual(self.service.project_id, self.replacement.id)
        self.assertIsNotNone(self.service.project_id)
        self.assertNotEqual(self.service.project_id, self.project.id)
