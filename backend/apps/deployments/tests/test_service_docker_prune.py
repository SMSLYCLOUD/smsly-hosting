from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.models.addons import Addon


class ServiceDockerPruneTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="service-prune", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="prune-local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="prune-svc", owner=self.user, provider=self.provider,
        )
        self.failed = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash="failed",
            container_id="failed-container",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_prune_is_service_scoped_and_preserves_active_rows(self, mock_client):
        client = MagicMock()
        client.containers.get.side_effect = lambda _name: MagicMock()
        client.images.prune.return_value = {"SpaceReclaimed": 1024}
        mock_client.return_value = client
        active = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash="active",
        )

        response = self.client.post(
            f"/api/v1/services/{self.service.id}/prune-docker/",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Deployment.objects.filter(pk=self.failed.pk).exists())
        self.assertTrue(Deployment.objects.filter(pk=active.pk).exists())
        client.containers.prune.assert_not_called()
        client.images.prune.assert_called_once_with(filters={"dangling": ["true"]})
