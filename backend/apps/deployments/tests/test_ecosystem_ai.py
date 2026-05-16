from django.test import TestCase
from unittest.mock import patch
from apps.deployments.services.ecosystem_ai import EcosystemDeploymentSenate
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
from pydantic import ValidationError

class TestEcosystemAI(TestCase):
    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_valid_json_accepted(self, mock_ask):
        mock_ask.return_value = '{"resolutions": {"api": {"PORT": "8000"}}}'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertIsNotNone(res)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_markdown_wrapped_json_extracted(self, mock_ask):
        mock_ask.return_value = '```json\n{"resolutions": {"api": {"PORT": "8000"}}}\n```'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_trailing_commas_repaired(self, mock_ask):
        mock_ask.return_value = '{"resolutions": {"api": {"PORT": "8000",},},}'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_mixed_prose_json_extracted(self, mock_ask):
        mock_ask.return_value = 'Here is the result:\n{"resolutions": {"api": {"PORT": "8000"}}}\nHope this helps!'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_wrong_types_normalized(self, mock_ask):
        mock_ask.return_value = '{"resolutions": {"api": {"PORT": 8000}}}'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_missing_optional_fields_defaulted(self, mock_ask):
        mock_ask.return_value = '{}'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"], {})

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_truncated_response_retried_and_recovered(self, mock_ask):
        mock_ask.side_effect = ['{"resolutions": {"api": {"PORT": "8000"', '{"resolutions": {"api": {"PORT": "8000"}}}']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_invalid_schema_retried(self, mock_ask):
        mock_ask.side_effect = ['{"invalid_key": "data"}', '{"resolutions": {"api": {"PORT": "8000"}}}']
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    type: backend')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["PORT"], "8000")

    @patch('apps.deployments.services.ecosystem_ai.ask_with_fallback')
    def test_heuristic_fallback_works(self, mock_ask):
        mock_ask.return_value = 'I cannot do this.'
        graph = build_ecosystem_graph('version: "1"\nservices:\n  api:\n    env:\n      FOO:\n        source: external_required')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertEqual(res["resolutions"]["api"]["FOO"], "fallback_FOO")
