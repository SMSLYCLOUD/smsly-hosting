# pylint: disable=invalid-name
"""Tests to verify existing topology functionality still works after ecosystem changes."""

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import Region, Service
from apps.deployments.services.graph_builder import GraphBuilder


class ExistingGraphBuilderTests(TestCase):
    """Verify the original GraphBuilder (per-user service topology) still works."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='topology-test',
            email='topo@test.com',
            password='password123',
        )
        self.region = Region.objects.create(name='Test Region', slug='test-region')

    def test_graph_builder_returns_nodes_and_edges(self):
        Service.objects.create(
            name='test-service',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT',
        )
        builder = GraphBuilder(self.user)
        graph = builder.build()
        self.assertIn('nodes', graph)
        self.assertIn('edges', graph)

    def test_graph_builder_includes_user_services(self):
        service = Service.objects.create(
            name='my-app',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT',
        )
        builder = GraphBuilder(self.user)
        graph = builder.build()
        node_ids = [n['id'] for n in graph['nodes']]
        self.assertIn(str(service.id), node_ids)

    def test_graph_builder_excludes_other_users_services(self):
        other_user = User.objects.create_user(
            username='other-user',
            email='other@test.com',
            password='password123',
        )
        my_service = Service.objects.create(
            name='my-service',
            owner=self.user,
            primary_region=self.region,
            deploy_type='GIT',
        )
        other_service = Service.objects.create(
            name='other-service',
            owner=other_user,
            primary_region=self.region,
            deploy_type='GIT',
        )
        builder = GraphBuilder(self.user)
        graph = builder.build()
        node_ids = [n['id'] for n in graph['nodes']]
        self.assertIn(str(my_service.id), node_ids)
        self.assertNotIn(str(other_service.id), node_ids)


class ExistingTopologyAPITests(APITestCase):
    """Verify the existing /api/v1/topology/ endpoint still works."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='topo-api-test',
            email='topoapi@test.com',
            password='password123',
        )

    def test_topology_endpoint_requires_auth(self):
        res = self.client.get('/api/v1/topology/')
        self.assertIn(res.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_topology_endpoint_returns_graph(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/v1/topology/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('nodes', res.data)
        self.assertIn('edges', res.data)

    def test_topology_endpoint_returns_graph_structure(self):
        """The topology endpoint returns a graph with nodes and edges."""
        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/v1/topology/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Returns a dict with nodes/edges
        self.assertIn('nodes', res.data)
        self.assertIn('edges', res.data)


class TopologyViewImportsTests(TestCase):
    """Verify topology view imports are intact after changes."""

    def test_ecosystem_action_exists(self):
        from apps.deployments.views.topology import TopologyViewSet
        self.assertTrue(hasattr(TopologyViewSet, 'ecosystem'))

    def test_list_action_exists(self):
        from apps.deployments.views.topology import TopologyViewSet
        self.assertTrue(hasattr(TopologyViewSet, 'list'))

    def test_ecosystem_graph_builder_importable(self):
        from apps.deployments.services.ecosystem_graph_builder import (
            EcosystemGraphBuilder,
        )
        builder = EcosystemGraphBuilder()
        self.assertIsNotNone(builder)

    def test_graph_builder_importable(self):
        from apps.deployments.services.graph_builder import GraphBuilder
        self.assertIsNotNone(GraphBuilder)


class TopologyTypesTests(TestCase):
    """Verify frontend types file has correct structure."""

    def test_types_file_exists(self):
        import os
        types_path = os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '..',
            'frontend', 'src', 'types', 'topology.ts'
        )
        self.assertTrue(os.path.exists(types_path))

    def test_types_file_has_ecosystem_types(self):
        import os
        types_path = os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '..',
            'frontend', 'src', 'types', 'topology.ts'
        )
        if not os.path.exists(types_path):
            self.skipTest("topology.ts not found")
        with open(types_path) as f:
            content = f.read()
        self.assertIn('EcosystemNode', content)
        self.assertIn('EcosystemEdge', content)
        self.assertIn('EcosystemGraph', content)
