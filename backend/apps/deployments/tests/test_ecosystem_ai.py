import unittest
from unittest.mock import patch, MagicMock
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))

# Mock apps.intelligence
sys.modules['apps.intelligence'] = MagicMock()
sys.modules['apps.intelligence.providers'] = MagicMock()

from apps.deployments.services.ecosystem_ai import EcosystemDeploymentSenate
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

class TestEcosystemAI(unittest.TestCase):
    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
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

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_propose_env_resolution_fallback(self, mock_ask):
        mock_ask.return_value = "I am an AI. I cannot output JSON."
        graph = build_ecosystem_graph('version: "1"')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertIsNone(res)

if __name__ == '__main__':
    unittest.main()
