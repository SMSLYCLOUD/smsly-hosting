# pylint: disable=invalid-name
"""Tests for health endpoint."""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from unittest.mock import patch


class HealthEndpointTests(TestCase):
    """Test health check endpoint functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.client = Client()
    
    def test_health_endpoint_returns_200_when_healthy(self):
        """Health endpoint should return 200 when all systems are operational."""
        response = self.client.get('/health')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'healthy')
        self.assertEqual(response.json()['database'], 'healthy')
        self.assertEqual(response.json()['cache'], 'healthy')
    
    @patch('django.db.connection.cursor')
    def test_health_endpoint_returns_503_when_database_down(self, mock_cursor):
        """Health endpoint should return 503 when database is unreachable."""
        mock_cursor.side_effect = Exception('Database connection failed')
        
        response = self.client.get('/health')
        
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unhealthy')
        self.assertEqual(response.json()['database'], 'unhealthy')
    
    @patch('django.core.cache.cache.set')
    def test_health_endpoint_returns_503_when_cache_down(self, mock_cache_set):
        """Health endpoint should return 503 when Redis/cache is unreachable."""
        mock_cache_set.side_effect = Exception('Cache connection failed')
        
        response = self.client.get('/health')
        
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unhealthy')
        self.assertEqual(response.json()['cache'], 'unhealthy')
    
    def test_health_endpoint_does_not_require_authentication(self):
        """Health endpoint should be accessible without authentication."""
        # No authentication headers
        response = self.client.get('/health')
        
        # Should still return 200 (load balancers need unauthenticated access)
        self.assertIn(response.status_code, [200, 503])  # Either healthy or unhealthy, but not 401/403
    
    def test_health_endpoint_response_format(self):
        """Health endpoint should return JSON with expected fields."""
        response = self.client.get('/health')
        
        data = response.json()
        self.assertIn('status', data)
        self.assertIn('database', data)
        self.assertIn('cache', data)
        self.assertIn(data['status'], ['healthy', 'unhealthy'])
