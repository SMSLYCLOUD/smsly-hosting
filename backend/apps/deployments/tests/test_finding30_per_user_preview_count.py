from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views import safedeploy as views_safedeploy
from apps.deployments.models.core import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


class Finding30PerUserPreviewCountTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='f30per-owner', password='p',
        )
        self.service = Service.objects.create(
            name='f30per-svc', owner=self.owner,
            preview_environments_enabled=True,
        )
        self.url = f'/api/v1/services/{self.service.id}/previews/'
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self._original_quota = views_safedeploy.MAX_PREVIEWS_PER_CREATOR
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = 2

    def tearDown(self):
        views_safedeploy.MAX_PREVIEWS_PER_CREATOR = self._original_quota

    def _create_preview(self, user, branch, sha):
        from apps.deployments.services.safedeploy.branch_preview_manager import (
            BranchPreviewManager,
        )
        return BranchPreviewManager().create_preview(
            self.service, branch, sha, user=user,
        )

    def _set_status(self, preview, status_value):
        preview.status = status_value
        preview.save()

    @patch('apps.deployments.tasks_safedeploy.create_preview_environment_job.delay')
    def test_create_view_filters_active_count_by_created_by(self, mock_delay):
        mock_delay.return_value = None
        for idx in range(2):
            preview = self._create_preview(
                self.owner, f'main-{idx}', f'aa{idx:05d}aaa',
            )
            self._set_status(preview, PreviewEnvironment.Status.BUILDING)

        resp = self.client.post(
            self.url,
            {'branch_name': 'fresh', 'commit_sha': 'bbb2222'},
            format='json',
        )
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Per-user preview quota', str(resp.data))

    @patch('apps.deployments.tasks_safedeploy.create_preview_environment_job.delay')
    def test_other_user_active_previews_do_not_block_first_creator(self, mock_delay):
        mock_delay.return_value = None
        other = User.objects.create_user(
            username='f30per-other', password='p',
        )
        for idx in range(2):
            preview = self._create_preview(
                other, f'other-{idx}', f'ccc{idx:04d}ddd',
            )
            self._set_status(preview, PreviewEnvironment.Status.BUILDING)

        resp = self.client.post(
            self.url,
            {'branch_name': 'mine-1', 'commit_sha': 'eee1111'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)

    @patch('apps.deployments.tasks_safedeploy.create_preview_environment_job.delay')
    def test_other_user_previews_do_not_count_toward_first_creator_quota(self, mock_delay):
        mock_delay.return_value = None
        other = User.objects.create_user(
            username='f30per-other2', password='p',
        )
        for idx in range(2):
            preview = self._create_preview(
                other, f'other-active-{idx}', f'aaa{idx:04d}bbb',
            )
            self._set_status(preview, PreviewEnvironment.Status.READY)

        for idx in range(2):
            preview = self._create_preview(
                self.owner, f'mine-{idx}', f'ccc{idx:04d}ddd',
            )
            self._set_status(preview, PreviewEnvironment.Status.READY)

        resp = self.client.post(
            self.url,
            {'branch_name': 'mine-3', 'commit_sha': '1111aaa'},
            format='json',
        )
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Per-user preview quota', str(resp.data))
