from django.test import TestCase

from apps.deployments.services.ecosystem_ai import EcosystemDeploymentSenate
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph


class TestEcosystemAISafe(TestCase):
    def test_propose_env_resolution(self):
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

    def test_propose_env_resolution_fallback(self):
        graph = build_ecosystem_graph('version: "1"')
        res = EcosystemDeploymentSenate.propose_env_resolution(graph)
        self.assertIsNone(res)
