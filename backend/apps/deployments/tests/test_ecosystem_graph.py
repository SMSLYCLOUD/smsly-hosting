from django.test import TestCase
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

class EcosystemGraphTests(TestCase):
    def test_simple_dependency(self):
        manifest = """
        services:
          backend:
            image: backend:latest
          frontend:
            image: frontend:latest
            dependencies:
              - backend
        """
        graph = build_ecosystem_graph(manifest)
        order = graph.get_topological_order()
        self.assertEqual(order, ['backend', 'frontend'])

    def test_circular_dependency(self):
        manifest = """
        services:
          a:
            dependencies: [b]
          b:
            dependencies: [c]
          c:
            dependencies: [a]
        """
        graph = build_ecosystem_graph(manifest)
        with self.assertRaises(ValueError) as cm:
            graph.get_topological_order()
        self.assertIn("Circular dependency detected", str(cm.exception))

    def test_missing_dependency_ignored(self):
        manifest = """
        services:
          frontend:
            dependencies: [backend]
        """
        graph = build_ecosystem_graph(manifest)
        order = graph.get_topological_order()
        self.assertEqual(order, ['frontend'])

    def test_complex_graph(self):
        manifest = """
        services:
          db: {}
          redis: {}
          backend:
            dependencies: [db, redis]
          worker:
            dependencies: [backend, redis]
          frontend:
            dependencies: [backend]
        """
        graph = build_ecosystem_graph(manifest)
        order = graph.get_topological_order()
        self.assertTrue(order.index('db') < order.index('backend'))
        self.assertTrue(order.index('redis') < order.index('backend'))
        self.assertTrue(order.index('backend') < order.index('worker'))
        self.assertTrue(order.index('backend') < order.index('frontend'))
