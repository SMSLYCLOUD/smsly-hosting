import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph
from apps.deployments.services.ecosystem_env import EcosystemEnvResolver, is_weak_value

class TestEcosystemEnv(unittest.TestCase):
    def test_weak_values(self):
        self.assertTrue(is_weak_value("changeme"))
        self.assertTrue(is_weak_value("my_SECRET_123"))
        self.assertFalse(is_weak_value("kdjf8394jf9834jf984"))

    def test_resolver(self):
        manifest_yaml = """
        version: "1"
        mode: "production"
        shared_env:
          groups:
            core:
              vars:
                JWT_SECRET:
                  source: generated
        services:
          api:
            env:
              JWT_SECRET:
                source: shared_group
                group: core
              EXTERNAL_KEY:
                source: external_required
                required: true
        """
        graph = build_ecosystem_graph(manifest_yaml)
        resolver = EcosystemEnvResolver(graph)
        success, envs, errors = resolver.validate_and_resolve()

        self.assertFalse(success)
        self.assertIn("Service 'api' missing external required env 'EXTERNAL_KEY'", errors)

if __name__ == '__main__':
    unittest.main()
