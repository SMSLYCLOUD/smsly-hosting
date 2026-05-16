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

    def test_deterministic_ordering(self):
        manifest1 = """
        services:
          z_svc: {}
          a_svc: {}
          m_svc: {}
        """
        manifest2 = """
        services:
          m_svc: {}
          z_svc: {}
          a_svc: {}
        """
        graph1 = build_ecosystem_graph(manifest1)
        graph2 = build_ecosystem_graph(manifest2)
        self.assertEqual(graph1.get_topological_order(), ['a_svc', 'm_svc', 'z_svc'])
        self.assertEqual(graph2.get_topological_order(), ['a_svc', 'm_svc', 'z_svc'])

    def test_parallel_deployment_where_possible(self):
        manifest = """
        services:
          c_backend:
            dependencies: [a_db]
          b_backend:
            dependencies: [a_db]
          a_db: {}
        """
        graph = build_ecosystem_graph(manifest)
        order = graph.get_topological_order()
        self.assertEqual(order, ['a_db', 'b_backend', 'c_backend'])

    def test_graph_serialization_stable(self):
        # Even with nested complex dependencies, result should be identical between identical runs.
        manifest = """
        services:
          svc1:
            dependencies: [svc3, svc2]
          svc2: {}
          svc3: {}
        """
        graph = build_ecosystem_graph(manifest)
        order1 = graph.get_topological_order()
        order2 = graph.get_topological_order()
        self.assertEqual(order1, order2)
        self.assertEqual(order1, ['svc2', 'svc3', 'svc1'])
