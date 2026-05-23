# pylint: disable=invalid-name
"""Tests for the platform ecosystem topology graph builder and API."""

from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.services.ecosystem_graph_builder import (
    EcosystemGraphBuilder,
    _check_tcp,
    _redis_host_port,
    _rabbitmq_host_port,
    _db_host_port,
)

# All tests mock _check_tcp to avoid real network calls
MOCK_TCP = patch('apps.deployments.services.ecosystem_graph_builder._check_tcp', return_value=True)


class EcosystemGraphBuilderNodeTests(TestCase):
    """Verify EcosystemGraphBuilder produces correct nodes."""

    @MOCK_TCP
    def test_build_returns_nodes_and_edges(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        self.assertIn('nodes', graph)
        self.assertIn('edges', graph)
        self.assertIsInstance(graph['nodes'], list)
        self.assertIsInstance(graph['edges'], list)

    @MOCK_TCP
    def test_all_expected_nodes_present(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        node_ids = {n['id'] for n in graph['nodes']}
        expected = {
            'internet', 'caddy', 'traefik', 'backend', 'frontend',
            'celery-default', 'celery-fast', 'celery-deploy', 'celery-beat',
            'postgresql', 'redis', 'rabbitmq', 'socket-proxy', 'registry',
            'frps', 'user-containers',
        }
        self.assertEqual(node_ids, expected)

    @MOCK_TCP
    def test_each_node_has_required_fields(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for node in graph['nodes']:
            self.assertIn('id', node, f"Node missing 'id': {node}")
            self.assertIn('type', node, f"Node {node.get('id')} missing 'type'")
            self.assertIn('kind', node, f"Node {node.get('id')} missing 'kind'")
            self.assertIn('label', node, f"Node {node.get('id')} missing 'label'")
            self.assertIn('status', node, f"Node {node.get('id')} missing 'status'")
            self.assertIn('metadata', node, f"Node {node.get('id')} missing 'metadata'")

    @MOCK_TCP
    def test_node_types_are_valid(self, _):
        valid_types = {
            'external', 'proxy', 'platform', 'worker', 'platform_db',
            'platform_cache', 'broker', 'registry', 'tunnel', 'service',
        }
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for node in graph['nodes']:
            self.assertIn(
                node['type'], valid_types,
                f"Node {node['id']} has invalid type: {node['type']}"
            )

    @MOCK_TCP
    def test_node_kinds_are_valid(self, _):
        valid_kinds = {
            'EXTERNAL', 'PROXY', 'COMPUTE', 'DATABASE', 'CACHE',
            'QUEUE', 'WORKER', 'STORAGE',
        }
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for node in graph['nodes']:
            self.assertIn(
                node['kind'], valid_kinds,
                f"Node {node['id']} has invalid kind: {node['kind']}"
            )

    @MOCK_TCP
    def test_node_status_is_valid(self, _):
        valid_statuses = {'healthy', 'degraded', 'down'}
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for node in graph['nodes']:
            self.assertIn(
                node['status'], valid_statuses,
                f"Node {node['id']} has invalid status: {node['status']}"
            )


class EcosystemGraphBuilderEdgeTests(TestCase):
    """Verify EcosystemGraphBuilder produces correct edges."""

    @MOCK_TCP
    def test_all_expected_edges_present(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        edge_keys = {(e['source'], e['target']) for e in graph['edges']}
        expected = {
            ('internet', 'caddy'),
            ('caddy', 'backend'),
            ('caddy', 'frontend'),
            ('caddy', 'traefik'),
            ('traefik', 'user-containers'),
            ('backend', 'postgresql'),
            ('backend', 'redis'),
            ('backend', 'socket-proxy'),
            ('socket-proxy', 'user-containers'),
            ('backend', 'registry'),
            ('celery-default', 'rabbitmq'),
            ('celery-fast', 'rabbitmq'),
            ('celery-deploy', 'rabbitmq'),
            ('celery-beat', 'rabbitmq'),
            ('backend', 'rabbitmq'),
            ('celery-default', 'postgresql'),
            ('celery-fast', 'postgresql'),
            ('celery-deploy', 'postgresql'),
            ('celery-deploy', 'socket-proxy'),
            ('celery-deploy', 'registry'),
            ('frps', 'caddy'),
        }
        self.assertEqual(edge_keys, expected)

    @MOCK_TCP
    def test_each_edge_has_required_fields(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for edge in graph['edges']:
            self.assertIn('source', edge, f"Edge missing 'source': {edge}")
            self.assertIn('target', edge, f"Edge missing 'target': {edge}")
            self.assertIn('type', edge, f"Edge {edge.get('source')}->{edge.get('target')} missing 'type'")

    @MOCK_TCP
    def test_edge_types_are_valid(self, _):
        valid_types = {'PROXY_CHAIN', 'DATABASE', 'CACHE', 'QUEUE', 'INTERNAL', 'TUNNEL'}
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        for edge in graph['edges']:
            self.assertIn(
                edge['type'], valid_types,
                f"Edge {edge['source']}->{edge['target']} has invalid type: {edge['type']}"
            )

    @MOCK_TCP
    def test_edge_references_valid_nodes(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        node_ids = {n['id'] for n in graph['nodes']}
        for edge in graph['edges']:
            self.assertIn(
                edge['source'], node_ids,
                f"Edge source '{edge['source']}' not in nodes"
            )
            self.assertIn(
                edge['target'], node_ids,
                f"Edge target '{edge['target']}' not in nodes"
            )

    @MOCK_TCP
    def test_internet_caddy_edge_is_animated(self, _):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        internet_caddy = next(
            e for e in graph['edges']
            if e['source'] == 'internet' and e['target'] == 'caddy'
        )
        self.assertTrue(internet_caddy.get('animated'))


class EcosystemGraphBuilderHealthCheckTests(TestCase):
    """Test health check logic."""

    @patch('apps.deployments.services.ecosystem_graph_builder._check_tcp', return_value=True)
    def test_healthy_service_returns_healthy(self, mock_tcp):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        caddy = next(n for n in graph['nodes'] if n['id'] == 'caddy')
        self.assertEqual(caddy['status'], 'healthy')

    @patch('apps.deployments.services.ecosystem_graph_builder._check_tcp', return_value=False)
    def test_unreachable_service_returns_down(self, mock_tcp):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        caddy = next(n for n in graph['nodes'] if n['id'] == 'caddy')
        self.assertEqual(caddy['status'], 'down')

    @MOCK_TCP
    def test_service_without_health_check_returns_healthy(self, _):
        """Services like celery workers have no health_check — default to healthy."""
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        celery = next(n for n in graph['nodes'] if n['id'] == 'celery-default')
        self.assertEqual(celery['status'], 'healthy')

    @patch('apps.deployments.services.ecosystem_graph_builder._check_tcp', side_effect=Exception('timeout'))
    def test_health_check_exception_returns_degraded(self, mock_tcp):
        builder = EcosystemGraphBuilder()
        graph = builder.build()
        caddy = next(n for n in graph['nodes'] if n['id'] == 'caddy')
        self.assertEqual(caddy['status'], 'degraded')


class TcpCheckTests(TestCase):
    """Test the _check_tcp helper."""

    @patch('apps.deployments.services.ecosystem_graph_builder.socket.create_connection')
    def test_returns_true_on_success(self, mock_conn):
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        self.assertTrue(_check_tcp('localhost', 8000))

    @patch('apps.deployments.services.ecosystem_graph_builder.socket.create_connection', side_effect=OSError)
    def test_returns_false_on_failure(self, mock_conn):
        self.assertFalse(_check_tcp('localhost', 9999))


class HostPortExtractionTests(TestCase):
    """Test helper functions that extract host/port from Django settings."""

    @override_settings(REDIS_HOST='custom-redis', REDIS_PORT='6380')
    def test_redis_host_port_from_settings(self):
        host, port = _redis_host_port()
        self.assertEqual(host, 'custom-redis')
        self.assertEqual(port, 6380)

    def test_redis_host_port_defaults(self):
        host, port = _redis_host_port()
        self.assertEqual(host, 'redis')
        self.assertEqual(port, 6379)

    def test_db_host_port_from_databases(self):
        with override_settings(DATABASES={'default': {'HOST': 'custom-db', 'PORT': '5433'}}):
            host, port = _db_host_port()
            self.assertEqual(host, 'custom-db')
            self.assertEqual(port, 5433)

    def test_db_host_port_defaults(self):
        with override_settings(DATABASES={'default': {'HOST': 'db'}}):
            host, port = _db_host_port()
            self.assertEqual(host, 'db')
            self.assertEqual(port, 5432)


class EcosystemTopologyAPITests(APITestCase):
    """Test the /api/v1/topology/ecosystem/ endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='ecosystem-test',
            email='eco@test.com',
            password='password123',
        )

    def test_endpoint_requires_authentication(self):
        res = self.client.get('/api/v1/topology/ecosystem/')
        self.assertIn(res.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    @MOCK_TCP
    def test_endpoint_returns_graph(self, _):
        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/v1/topology/ecosystem/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('nodes', res.data)
        self.assertIn('edges', res.data)
        self.assertGreater(len(res.data['nodes']), 0)
        self.assertGreater(len(res.data['edges']), 0)

    @MOCK_TCP
    def test_endpoint_returns_all_infrastructure_nodes(self, _):
        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/v1/topology/ecosystem/')
        node_ids = {n['id'] for n in res.data['nodes']}
        self.assertIn('caddy', node_ids)
        self.assertIn('backend', node_ids)
        self.assertIn('frontend', node_ids)
        self.assertIn('postgresql', node_ids)
        self.assertIn('redis', node_ids)
        self.assertIn('rabbitmq', node_ids)

    @MOCK_TCP
    def test_endpoint_includes_traffic_flow_animation_flag(self, _):
        self.client.force_authenticate(user=self.user)
        res = self.client.get('/api/v1/topology/ecosystem/')
        internet_edge = next(
            e for e in res.data['edges']
            if e['source'] == 'internet' and e['target'] == 'caddy'
        )
        self.assertTrue(internet_edge.get('animated'))
