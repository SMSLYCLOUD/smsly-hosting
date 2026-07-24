from django.contrib.auth import get_user_model
from django.test import TestCase

from ..models import EnvironmentVariable, Region, Service  # type: ignore[attr-defined]
from ..models.addons import Addon
from ..services.graph_builder import GraphBuilder

User = get_user_model()

class GraphBuilderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.region = Region.objects.create(name='Test Region', slug='test-region')

        # Service 1: App
        self.service_app = Service.objects.create(
            name='app-service',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT'
        )

        # Service 2: Database (Compute based, e.g. self-hosted)
        self.service_db = Service.objects.create(
            name='db-service',
            owner=self.user,
            primary_region=self.region,
            deploy_type='DOCKER'
        )

    def test_build_nodes_basic(self):
        builder = GraphBuilder(self.user)
        graph = builder.build()

        node_ids = [n['id'] for n in graph['nodes']]
        self.assertIn(str(self.service_app.id), node_ids)
        self.assertIn(str(self.service_db.id), node_ids)

        # Check node data
        app_node = next(n for n in graph['nodes'] if n['id'] == str(self.service_app.id))
        self.assertEqual(app_node['type'], 'SERVICE')
        self.assertEqual(app_node['data']['name'], 'app-service')

    def test_build_addon_nodes(self):
        # Add an addon to app-service
        addon = Addon.objects.create(
            service=self.service_app,
            name='my-redis',
            addon_type='REDIS',
            status='ACTIVE'
        )

        builder = GraphBuilder(self.user)
        graph = builder.build()

        addon_id = f"addon-{addon.id}"
        node_ids = [n['id'] for n in graph['nodes']]
        self.assertIn(addon_id, node_ids)

        # Check OWNS edge
        edge = next((e for e in graph['edges'] if e['target'] == addon_id), None)
        self.assertIsNotNone(edge)
        self.assertEqual(edge['source'], str(self.service_app.id))
        self.assertEqual(edge['type'], 'OWNS')

    def test_infer_url_connection(self):
        # app-service connects to db-service via URL
        EnvironmentVariable.objects.create(
            service=self.service_app,
            key='DATABASE_URL',
            value='postgres://user:pass@db-service:5432/db'
        )

        builder = GraphBuilder(self.user)
        graph = builder.build()

        # Check CONNECTS_TO edge
        edge = next((e for e in graph['edges']
                     if e['source'] == str(self.service_app.id)
                     and e['target'] == str(self.service_db.id)), None)

        self.assertIsNotNone(edge)
        self.assertEqual(edge['type'], 'CONNECTS_TO')
        self.assertEqual(edge['data']['protocol'], 'postgres')
        self.assertEqual(edge['data']['evidence'], 'DATABASE_URL')

    def test_infer_host_connection(self):
        # app-service connects to db-service via Hostname
        EnvironmentVariable.objects.create(
            service=self.service_app,
            key='DB_HOST',
            value='db-service'
        )

        builder = GraphBuilder(self.user)
        graph = builder.build()

        edge = next((e for e in graph['edges']
                     if e['source'] == str(self.service_app.id)
                     and e['target'] == str(self.service_db.id)), None)

        self.assertIsNotNone(edge)
        self.assertEqual(edge['type'], 'CONNECTS_TO')
        self.assertEqual(edge['data']['protocol'], 'tcp')

    def test_infer_connection_to_addon(self):
        # Addon on db-service? No, usually distinct.
        # Let's say app-service connects to an addon owned by itself (e.g. redis)
        addon = Addon.objects.create(
            service=self.service_app,
            name='cache-redis', # Unique name
            addon_type='REDIS'
        )

        EnvironmentVariable.objects.create(
            service=self.service_app,
            key='REDIS_URL',
            value='redis://cache-redis:6379'
        )

        builder = GraphBuilder(self.user)
        graph = builder.build()

        addon_id = f"addon-{addon.id}"

        # Should have OWNS edge
        owns_edge = next((e for e in graph['edges'] if e['type'] == 'OWNS' and e['target'] == addon_id), None)
        self.assertIsNotNone(owns_edge)

        # Should have CONNECTS_TO edge (self-reference? GraphBuilder prevents self-service links, but allow self-addon?)
        # Let's check logic:
        # _match_and_link(service, 'cache-redis', ...)
        # target is addon.
        # target_id is addon-id.
        # source is service-id.
        # edge_id = service-addon.
        # If OWNS edge has different ID format, it's fine.
        # OWNS edge ID: `owns-{service.id}-{addon.id}`
        # CONNECTS edge ID: `{service.id}-{addon.id}`
        # So they are distinct edges.

        connects_edge = next((e for e in graph['edges']
                              if e['type'] == 'CONNECTS_TO'
                              and e['target'] == addon_id), None)

        self.assertIsNotNone(connects_edge)
