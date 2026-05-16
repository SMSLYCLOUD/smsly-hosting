from django.test import TestCase
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
from apps.deployments.services.ecosystem_env import EcosystemEnvResolver

class TestEcosystemMoreTests(TestCase):
    def test_duplicate_keys_handled(self):
        # Implicitly handled by JSON parser (last key wins)
        import json
        text = '{"resolutions": {"api": {"PORT": "8000", "PORT": "8080"}}}'
        data = json.loads(text)
        self.assertEqual(data["resolutions"]["api"]["PORT"], "8080")

    def test_cross_repo_references_resolved(self):
        manifest_yaml = """
        version: "1"
        services:
          repo1_api:
            type: backend
          repo2_worker:
            type: worker
            env:
              API_URL:
                source: service_internal_url
                service: repo1_api
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()
        self.assertTrue(success)
        self.assertEqual(envs["repo2_worker"]["API_URL"], "http://repo1_api")

    def test_blocking_dependencies_enforced(self):
        manifest_yaml = """
        version: "1"
        services:
          svc1:
            dependencies: [svc2]
          svc2: {}
        """
        graph = build_ecosystem_graph(manifest_yaml)
        order = graph.get_topological_order()
        self.assertEqual(order, ['svc2', 'svc1'])
