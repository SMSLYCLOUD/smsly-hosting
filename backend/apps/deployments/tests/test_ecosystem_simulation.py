import os

from django.test import TestCase

from apps.deployments.services.ecosystem_env import EcosystemEnvResolver, is_weak_value
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph


class TestEcosystemSimulationSafe(TestCase):
    def load_fixture(self, name):
        path = os.path.join(os.path.dirname(__file__), 'fixtures/ecosystems', name)
        with open(path) as f:
            return f.read()

    def test_simple_backend_frontend(self):
        manifest = self.load_fixture('simple_backend_frontend.yml')
        graph = build_ecosystem_graph(manifest)

        self.assertIn('api', graph.services)
        self.assertIn('web', graph.services)

        resolver = EcosystemEnvResolver(graph)
        success, _envs, errors = resolver.validate_and_resolve()

        self.assertFalse(success)
        self.assertIn("Service 'api' missing external required env 'EXTERNAL_API_KEY'", "".join(errors))

    def test_missing_external_values(self):
        manifest = self.load_fixture('missing_external_values.yml')
        graph = build_ecosystem_graph(manifest)
        resolver = EcosystemEnvResolver(graph)
        success, _, errors = resolver.validate_and_resolve()
        self.assertFalse(success)
        self.assertTrue(any('STRIPE_SECRET_KEY' in e for e in errors))

    def test_invalid_placeholder(self):
        manifest = self.load_fixture('invalid_placeholder_env.yml')
        graph = build_ecosystem_graph(manifest)

        EcosystemEnvResolver(graph)

        self.assertTrue(is_weak_value("changeme"))
        self.assertTrue(is_weak_value("secret"))
