from django.test import TestCase
from unittest.mock import patch
from apps.deployments.services.ecosystem_ai import EcosystemDeploymentSenate
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

class TestEcosystemAISafe(TestCase):
    @patch('apps.deployments.services.ecosystem_ai._cached_ask')
    def test_propose_env_resolution(self, mock_ask):
        mock_ask.return_value = '{"resolutions": {"api": {"PORT": "8000"}}}'

        manifest_yaml = """
        version: "1"
        services:
          api:
            type: backend
        """
        graph = build_ecosystem_graph(manifest_yaml)

        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertIsNotNone(res)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai._cached_ask')
    def test_propose_env_resolution_fallback(self, mock_ask):
        mock_ask.return_value = "I am an AI. I cannot output JSON."
        graph = build_ecosystem_graph('version: "1"')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertIsNone(res)
