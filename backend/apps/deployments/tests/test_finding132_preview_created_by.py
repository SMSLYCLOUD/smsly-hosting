from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "preview-created-by-test",
    }
}


@override_settings(CACHES=TEST_CACHES)
class Finding132PreviewCreatedByTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='fix132', password='x', email='fix132@example.com',
        )
        self.service = Service.objects.create(
            name='fix132-svc',
            owner=self.owner,
            preview_environments_enabled=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_created_by_field_exists_on_model(self):
        from django.db import models
        field = PreviewEnvironment._meta.get_field('created_by')
        self.assertIsInstance(field, models.ForeignKey)
        self.assertEqual(field.remote_field.model, User)

    def test_create_action_binds_request_user_to_created_by(self):
        with patch(
            'apps.deployments.tasks_safedeploy.create_preview_environment_job.delay'
        ) as delay_mock:
            resp = self.client.post(
                f'/api/v1/services/{self.service.id}/previews/',
                data={
                    'branch_name': 'feature/x',
                    'commit_sha': 'a' * 40,
                },
                format='json',
            )
        self.assertEqual(resp.status_code, 201)
        delay_mock.assert_called_once()

        preview = PreviewEnvironment.objects.get(id=resp.data['id'])
        self.assertEqual(preview.created_by_id, self.owner.id)

    def test_manager_create_preview_accepts_user_kwarg(self):
        from apps.deployments.services.safedeploy.branch_preview_manager import (
            BranchPreviewManager,
        )
        manager = BranchPreviewManager()
        preview = manager.create_preview(
            self.service, 'main', 'b' * 40, user=self.owner,
        )
        self.assertEqual(preview.created_by_id, self.owner.id)
