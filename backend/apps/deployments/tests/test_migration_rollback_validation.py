from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.deployments.services.safedeploy.deployment_pipeline import (
    ProductionDeploymentPipeline,
)


class MigrationRollbackValidationTests(TestCase):
    def setUp(self):
        self.pipeline = ProductionDeploymentPipeline()

    def _make_deployment(self):
        deployment = MagicMock()
        deployment.id = "d-1"
        deployment.service = MagicMock()
        deployment.service.repository_url = ""
        env_var = MagicMock()
        env_var.key = "DATABASE_URL"
        env_var.value = "postgres://user:pass@db:5432/test"
        deployment.service.env_vars.all.return_value = [env_var]
        deployment.commit_hash = "abc"
        return deployment

    @patch("apps.deployments.services.safedeploy.django_adapter.DjangoAdapter")
    def test_invalid_app_label_is_skipped(self, mock_adapter_cls):
        mock_adapter = MagicMock()
        mock_adapter.detect.return_value = True
        mock_adapter.executor.run.return_value = (0, "", "")
        mock_adapter_cls.return_value = mock_adapter

        deployment = self._make_deployment()
        result = self.pipeline._attempt_migration_rollback(
            deployment,
            {"bad;rm -rf /": "0001_initial"},
        )
        self.assertFalse(result)
        mock_adapter.executor.run.assert_not_called()

    @patch("apps.deployments.services.safedeploy.django_adapter.DjangoAdapter")
    def test_invalid_migration_name_is_skipped(self, mock_adapter_cls):
        mock_adapter = MagicMock()
        mock_adapter.detect.return_value = True
        mock_adapter.executor.run.return_value = (0, "", "")
        mock_adapter_cls.return_value = mock_adapter

        deployment = self._make_deployment()
        result = self.pipeline._attempt_migration_rollback(
            deployment,
            {"myapp": "bad; curl evil"},
        )
        self.assertFalse(result)
        mock_adapter.executor.run.assert_not_called()

    @patch("apps.deployments.services.safedeploy.django_adapter.DjangoAdapter")
    @patch("apps.deployments.services.safedeploy.deployment_pipeline.DeploymentArtifact")
    def test_valid_inputs_are_passed_through(self, mock_artifact, mock_adapter_cls):
        mock_adapter = MagicMock()
        mock_adapter.detect.return_value = True
        mock_adapter.executor.run.return_value = (0, "", "")
        mock_adapter_cls.return_value = mock_adapter

        deployment = self._make_deployment()
        result = self.pipeline._attempt_migration_rollback(
            deployment,
            {"myapp": "0001_initial"},
        )
        self.assertTrue(result)
        mock_adapter.executor.run.assert_called_once()
        called_cmd = mock_adapter.executor.run.call_args[0][0]
        self.assertIn("myapp", called_cmd)
        self.assertIn("0001_initial", called_cmd)
