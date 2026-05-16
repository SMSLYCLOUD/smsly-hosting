from django.test import TestCase
from unittest.mock import patch, MagicMock
from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env
from apps.deployments.services.ecosystem_ai import EcosystemDeploymentSenate
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

class TestEcosystemFailureRecovery(TestCase):
    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_ai_malformed_response_recovered(self, mock_ask):
        # Starts with bad response, recovers on second attempt with valid JSON
        mock_ask.side_effect = ['{"resolutions": {"api": {"PORT": "8000"', '{"resolutions": {"api": {"PORT": "8000"}}}']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_retry_succeeds(self, mock_ask):
        # Initial response has a validation error (e.g. invalid array instead of dict)
        mock_ask.side_effect = ['{"resolutions": []}', '{"resolutions": {"api": {"PORT": "8000"}}}']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_fallback_prompt_succeeds(self, mock_ask):
        # We simulate the fallback prompt logic (when first fails)
        mock_ask.side_effect = ['invalid json completely', '{"resolutions": {"api": {"PORT": "8000"}}}']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_heuristic_inference_succeeds(self, mock_ask):
        # All AI attempts fail completely, it falls back to deterministic inference
        mock_ask.side_effect = ['bad', 'worse']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    env:\n      FOO:\n        source: external_required')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["FOO"], "fallback_FOO")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_clear_error_when_all_strategies_fail(self, mock_ask):
        mock_ask.side_effect = ['bad', 'worse']
        manifest_yaml = """
        version: "1"
        services:
          api:
            env:
              EXTERNAL_KEY:
                source: external_required
                required: true
        """
        # Testing full pipeline when inference fallback gives something that validation rejects
        success, msg = bulk_persist_and_verify_ecosystem_env(manifest_yaml, {"api": MagicMock()})
        self.assertFalse(success)
        self.assertIn("missing external required env 'EXTERNAL_KEY'", msg)
