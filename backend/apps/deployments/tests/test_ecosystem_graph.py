import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

class TestEcosystemGraph(unittest.TestCase):
    def test_topological_order(self):
        manifest_yaml = """
        version: "1"
        services:
          web:
            dependencies:
              - api
          api:
            dependencies:
              - redis
          worker:
            dependencies:
              - api
        """
        graph = build_ecosystem_graph(manifest_yaml)
        order = graph.get_topological_order()

        self.assertTrue(order.index('api') < order.index('web'))
        self.assertTrue(order.index('api') < order.index('worker'))

    def test_circular_dependency(self):
        manifest_yaml = """
        version: "1"
        services:
          service_a:
            dependencies:
              - service_b
          service_b:
            dependencies:
              - service_a
        """
        graph = build_ecosystem_graph(manifest_yaml)
        with self.assertRaises(ValueError):
            graph.get_topological_order()

if __name__ == '__main__':
    unittest.main()
