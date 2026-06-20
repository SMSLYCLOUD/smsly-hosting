from django.test import TestCase

from apps.deployments.services.ecosystem_graph import build_ecosystem_graph


class EcosystemStressTests(TestCase):
    def test_11_service_smsly_style_ecosystem(self):
        manifest = """
        services:
          frontend:
            dependencies: [api_gateway]
          api_gateway:
            dependencies: [auth_service, core_backend]
          auth_service:
            dependencies: [db_auth, redis]
          core_backend:
            dependencies: [db_main, redis, search_engine]
          worker_1:
            dependencies: [core_backend, queue]
          worker_2:
            dependencies: [core_backend, queue]
          db_auth: {}
          db_main: {}
          redis: {}
          queue: {}
          search_engine: {}
        """
        graph = build_ecosystem_graph(manifest)
        order = graph.get_topological_order()

        self.assertTrue(order.index('db_auth') < order.index('auth_service'))
        self.assertTrue(order.index('redis') < order.index('auth_service'))
        self.assertTrue(order.index('db_main') < order.index('core_backend'))
        self.assertTrue(order.index('search_engine') < order.index('core_backend'))
        self.assertTrue(order.index('core_backend') < order.index('worker_1'))
        self.assertTrue(order.index('api_gateway') < order.index('frontend'))
        self.assertEqual(len(order), 11)
