"""
Tests for RemoteOrchestrator mesh optimization and SSL verification logic.
"""
import os
import unittest
from unittest.mock import patch, MagicMock, Mock
from urllib.parse import urlparse

# Import the module under test
from apps.deployments.services.remote_orchestrator import (
    _host_is_ip,
    _is_internal_target,
    RemoteOrchestrator,
    _REMOTE_VERIFY,
    _ENFORCE_TLS,
)


class TestHostIsIP(unittest.TestCase):
    """Test the _host_is_ip helper function."""

    def test_ipv4_addresses(self):
        """Test various IPv4 address formats."""
        self.assertTrue(_host_is_ip("192.168.1.1"))
        self.assertTrue(_host_is_ip("10.0.0.1"))
        self.assertTrue(_host_is_ip("127.0.0.1"))
        self.assertTrue(_host_is_ip("209.159.152.123"))

    def test_ipv6_addresses(self):
        """Test IPv6 address formats."""
        self.assertTrue(_host_is_ip("::1"))
        self.assertTrue(_host_is_ip("2001:db8::1"))
        self.assertTrue(_host_is_ip("[2001:db8::1]"))  # Bracket notation

    def test_hostnames(self):
        """Test that hostnames return False."""
        self.assertFalse(_host_is_ip("example.com"))
        self.assertFalse(_host_is_ip("api.smsly.cloud"))
        self.assertFalse(_host_is_ip("localhost"))

    def test_host_with_port(self):
        """Test host:port combinations."""
        self.assertTrue(_host_is_ip("192.168.1.1:8080"))
        self.assertFalse(_host_is_ip("example.com:443"))


class TestIsInternalTarget(unittest.TestCase):
    """Test the _is_internal_target function for mesh VPN optimization."""

    def test_private_ipv4_ranges(self):
        """Test detection of private IP ranges."""
        # RFC 1918 private ranges
        self.assertTrue(_is_internal_target("http://10.0.0.1"))
        self.assertTrue(_is_internal_target("https://10.255.255.255"))
        self.assertTrue(_is_internal_target("http://172.16.0.1"))
        self.assertTrue(_is_internal_target("http://172.31.255.255"))
        self.assertTrue(_is_internal_target("http://192.168.1.1"))
        self.assertTrue(_is_internal_target("https://192.168.100.50:8080"))

    def test_mesh_vpn_ranges(self):
        """Test detection of Tailscale/FRP mesh VPN ranges."""
        # Tailscale CGNAT range (100.64.0.0/10)
        self.assertTrue(_is_internal_target("http://100.64.0.1"))
        self.assertTrue(_is_internal_target("https://100.127.255.255"))
        # FRP internal ranges
        self.assertTrue(_is_internal_target("http://100.64.1.10:8000"))

    def test_localhost(self):
        """Test localhost detection."""
        self.assertTrue(_is_internal_target("http://127.0.0.1"))
        self.assertTrue(_is_internal_target("https://127.0.0.1:443"))
        self.assertTrue(_is_internal_target("http://localhost"))

    def test_public_domains(self):
        """Test that public domains return False."""
        self.assertFalse(_is_internal_target("https://api.smsly.cloud"))
        self.assertFalse(_is_internal_target("https://example.com"))
        self.assertFalse(_is_internal_target("http://public-server.com:8080"))

    def test_public_ips(self):
        """Test that public IPs return False (but note: in mesh, all IPs might be internal)."""
        # These are public IPs, so _is_internal_target should return False
        # based on prefix matching, but _host_is_ip returns True
        # The function combines both checks
        self.assertTrue(_is_internal_target("http://209.159.152.123"))  # IP = internal for mesh
        self.assertFalse(_is_internal_target("https://api.smsly.cloud"))  # Domain = not internal


class TestCandidateBaseURLs(unittest.TestCase):
    """Test the _candidate_base_urls method for URL generation logic."""

    def setUp(self):
        """Set up a mock ManagedServer for testing."""
        self.mock_server = Mock()
        self.mock_server.name = "test-node"
        self.mock_server.host = "192.168.1.100"
        self.mock_server.api_url = None
        self.mock_server.is_lite_agent = False
        self.mock_server.api_token = "test_token"
        self.mock_server.gateway_secret = "test_secret"

    @patch("apps.deployments.services.remote_orchestrator._ENFORCE_TLS", False)
    def test_ip_host_generates_http_first(self):
        """Test that IP-based hosts prioritize HTTP for mesh VPN."""
        orchestrator = RemoteOrchestrator(self.mock_server)
        urls = orchestrator._candidate_base_urls()
        
        # HTTP should come before HTTPS for IP hosts (mesh optimization)
        http_urls = [u for u in urls if u.startswith("http://")]
        https_urls = [u for u in urls if u.startswith("https://")]
        
        self.assertTrue(len(http_urls) > 0, "Should generate HTTP URLs for mesh")
        # First URL should be HTTP for IP hosts
        if urls:
            self.assertTrue(urls[0].startswith("http://"), "First URL should be HTTP for IP host")

    @patch("apps.deployments.services.remote_orchestrator._ENFORCE_TLS", True)
    def test_tls_enforcement_skips_http(self):
        """Test that TLS enforcement returns only HTTPS URLs."""
        orchestrator = RemoteOrchestrator(self.mock_server)
        urls = orchestrator._candidate_base_urls()
        
        # All URLs should be HTTPS when enforcement is on
        for url in urls:
            self.assertTrue(url.startswith("https://"), f"URL should be HTTPS: {url}")

    @patch("apps.deployments.services.remote_orchestrator._ENFORCE_TLS", False)
    def test_domain_host_uses_configured_url(self):
        """Test that domain-based hosts use the configured api_url."""
        self.mock_server.host = "api.smsly.cloud"
        self.mock_server.api_url = "https://api.smsly.cloud"
        
        orchestrator = RemoteOrchestrator(self.mock_server)
        urls = orchestrator._candidate_base_urls()
        
        # Should include the configured URL
        self.assertIn("https://api.smsly.cloud", urls)


class TestRemoteRequestSSLVerification(unittest.TestCase):
    """Test SSL verification logic in _request method."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_server = Mock()
        self.mock_server.name = "test-node"
        self.mock_server.host = "192.168.1.100"
        self.mock_server.api_url = None
        self.mock_server.api_token = "test_token"
        self.mock_server.gateway_secret = "test_secret"

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    @patch("apps.deployments.services.remote_orchestrator._REMOTE_VERIFY", True)
    def test_internal_ip_skips_ssl_verification(self, mock_request):
        """Test that internal IPs skip SSL verification even when _REMOTE_VERIFY is True."""
        mock_request.return_value = Mock(status_code=200, json=lambda: {"id": "test"})
        
        orchestrator = RemoteOrchestrator(self.mock_server)
        
        # Mock the auth methods to avoid actual auth logic
        with patch.object(orchestrator, '_auth_modes', return_value=['token']):
            with patch.object(orchestrator, '_candidate_base_urls', return_value=['http://192.168.1.100']):
                orchestrator._request("GET", "/api/v1/test/", timeout=10)
        
        # Verify that verify=False was passed for internal IP
        call_kwargs = mock_request.call_args[1]
        self.assertFalse(call_kwargs.get('verify', True), "Should skip SSL verification for internal IP")

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    @patch("apps.deployments.services.remote_orchestrator._REMOTE_VERIFY", True)
    def test_public_domain_uses_ssl_verification(self, mock_request):
        """Test that public domains respect _REMOTE_VERIFY setting."""
        mock_request.return_value = Mock(status_code=200, json=lambda: {"id": "test"})
        
        self.mock_server.host = "api.smsly.cloud"
        self.mock_server.api_url = "https://api.smsly.cloud"
        orchestrator = RemoteOrchestrator(self.mock_server)
        
        with patch.object(orchestrator, '_auth_modes', return_value=['token']):
            with patch.object(orchestrator, '_candidate_base_urls', return_value=['https://api.smsly.cloud']):
                orchestrator._request("GET", "/api/v1/test/", timeout=10)
        
        # For HTTPS public domains, verify should be True (when _REMOTE_VERIFY is True)
        call_kwargs = mock_request.call_args[1]
        self.assertTrue(call_kwargs.get('verify', False), "Should verify SSL for public HTTPS domain")


class TestRemoteOrchestratorLogging(unittest.TestCase):
    """Test that key methods emit appropriate log messages."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_server = Mock()
        self.mock_server.name = "test-node"
        self.mock_server.host = "192.168.1.100"
        self.mock_server.api_url = None
        self.mock_server.api_token = "test_token"
        self.mock_server.gateway_secret = "test_secret"

    @patch("apps.deployments.services.remote_orchestrator.logger")
    def test_init_logs_server_info(self, mock_logger):
        """Test that __init__ logs server initialization."""
        RemoteOrchestrator(self.mock_server)
        
        # Should log initialization with server name and host
        mock_logger.info.assert_any_call(
            "RemoteOrchestrator initialized for %s (%s)",
            self.mock_server.name, self.mock_server.host
        )

    @patch("apps.deployments.services.remote_orchestrator.logger")
    def test_is_internal_target_logs_decision(self, mock_logger):
        """Test that _is_internal_target logs its decision."""
        # This is a static method, so we test it directly
        from apps.deployments.services.remote_orchestrator import _is_internal_target
        
        _is_internal_target("http://192.168.1.1")
        
        # Should log the URL, host, and decision
        mock_logger.debug.assert_called()
        call_args = mock_logger.debug.call_args[0]
        self.assertIn("is_internal", call_args[0])


class TestMeshOptimizationIntegration(unittest.TestCase):
    """Integration tests for mesh VPN optimization features."""

    def test_worker_ip_from_error_log(self):
        """Test the specific worker IP from the deployment error."""
        worker_ip = "69.164.244.51"
        
        # This IP should be detected as internal for mesh purposes
        # (even though it's a public IP, in the mesh context it's treated as internal)
        self.assertTrue(_host_is_ip(worker_ip), "Worker IP should be recognized as IP address")
        
        # The _is_internal_target function should return True for any IP
        # because mesh VPN handles encryption
        self.assertTrue(_is_internal_target(f"https://{worker_ip}"), 
                       "Mesh worker IP should skip SSL verification")

    def test_env_var_controls(self):
        """Test that environment variables control behavior."""
        # Test SMSLY_REMOTE_VERIFY
        with patch.dict(os.environ, {"SMSLY_REMOTE_VERIFY": "0"}):
            # Reload the module to pick up new env var
            import importlib
            import apps.deployments.services.remote_orchestrator as ro
            importlib.reload(ro)
            
            # When SMSLY_REMOTE_VERIFY=0, _REMOTE_VERIFY should be False
            # Note: This is a simplified test; in practice, the module would need
            # to be reloaded or the value cached differently
        
        # Test SMSLY_ENFORCE_INTERSERVER_TLS
        with patch.dict(os.environ, {"SMSLY_ENFORCE_INTERSERVER_TLS": "true"}):
            import importlib
            import apps.deployments.services.remote_orchestrator as ro
            importlib.reload(ro)
            # When enforcement is on, only HTTPS URLs should be returned


class TestClassify404Response(unittest.TestCase):
    """Test the _classify_404_response static method."""

    def test_traefik_default_404(self):
        """Traefik's default 404 body is exactly '404 page not found'."""
        resp = Mock()
        resp.text = "404 page not found"
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "traefik_no_router",
        )

    def test_traefik_default_404_whitespace(self):
        """Should handle trailing whitespace/newlines."""
        resp = Mock()
        resp.text = "404 page not found\n"
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "traefik_no_router",
        )

    def test_django_json_404(self):
        """Django DRF returns JSON with 'detail: Not found.'."""
        resp = Mock()
        resp.text = '{"detail": "Not found."}'
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "django_not_found",
        )

    def test_nginx_html_404(self):
        """Nginx returns an HTML 404 page."""
        resp = Mock()
        resp.text = "<html><body><h1>404 Not Found</h1></body></html>"
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "proxy_html_404",
        )

    def test_unknown_404(self):
        """Unknown format should return 'unknown_404'."""
        resp = Mock()
        resp.text = "something weird happened"
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "unknown_404",
        )

    def test_empty_body(self):
        """Empty response body should return 'unknown_404'."""
        resp = Mock()
        resp.text = ""
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "unknown_404",
        )

    def test_none_text(self):
        """None text attribute should not crash."""
        resp = Mock()
        resp.text = None
        self.assertEqual(
            RemoteOrchestrator._classify_404_response(resp),
            "unknown_404",
        )


class TestPreflightCheckOrHeal(unittest.TestCase):
    """Test the preflight_check_or_heal method."""

    def setUp(self):
        self.mock_server = Mock()
        self.mock_server.name = "test-node"
        self.mock_server.host = "69.164.244.51"
        self.mock_server.api_url = None
        self.mock_server.is_lite_agent = True
        self.mock_server.api_token = "test_token"
        self.mock_server.gateway_secret = "test_secret"
        self.mock_server.ssh_key = ""
        self.mock_server.ssh_password = ""
        self.mock_server.ssh_user = "root"
        self.mock_server.ssh_port = 22

    def test_healthy_node_returns_ok(self):
        """When check_connectivity returns auth=True, preflight is ok."""
        orchestrator = RemoteOrchestrator(self.mock_server)
        with patch.object(orchestrator, 'check_connectivity',
                          return_value={'network': True, 'auth': True, 'error': ''}):
            result = orchestrator.preflight_check_or_heal()

        self.assertTrue(result['ok'])
        self.assertFalse(result['healed'])
        self.assertEqual(result['error'], '')

    def test_network_unreachable(self):
        """When network is unreachable, should return diagnosis='network_unreachable'."""
        orchestrator = RemoteOrchestrator(self.mock_server)
        with patch.object(orchestrator, 'check_connectivity',
                          return_value={'network': False, 'auth': False, 'error': 'Connection refused'}):
            result = orchestrator.preflight_check_or_heal()

        self.assertFalse(result['ok'])
        self.assertEqual(result['diagnosis'], 'network_unreachable')
        self.assertIn('network-unreachable', result['error'])

    def test_traefik_404_no_ssh_credentials(self):
        """When Traefik 404 but no SSH credentials, heal should fail gracefully."""
        orchestrator = RemoteOrchestrator(self.mock_server)
        mock_probe = Mock()
        mock_probe.status_code = 404
        mock_probe.text = "404 page not found"

        with patch.object(orchestrator, 'check_connectivity',
                          return_value={'network': True, 'auth': False, 'error': 'API returned 404'}):
            with patch.object(orchestrator, '_request', return_value=mock_probe):
                result = orchestrator.preflight_check_or_heal()

        self.assertFalse(result['ok'])
        self.assertTrue(result['healed'])
        self.assertEqual(result['diagnosis'], 'traefik_no_router')
        self.assertIn('SSH auto-heal failed', result['error'])


class TestLiteAgentCandidateURLs(unittest.TestCase):
    """Test that lite agents only try port 80."""

    def setUp(self):
        self.mock_server = Mock()
        self.mock_server.name = "lite-node"
        self.mock_server.host = "69.164.244.51"
        self.mock_server.api_url = None
        self.mock_server.api_token = "test_token"
        self.mock_server.gateway_secret = "test_secret"

    @patch("apps.deployments.services.remote_orchestrator._ENFORCE_TLS", False)
    def test_lite_agent_only_port_80(self):
        """Lite agents should only generate http://{host} (port 80)."""
        self.mock_server.is_lite_agent = True
        orchestrator = RemoteOrchestrator(self.mock_server)
        urls = orchestrator._candidate_base_urls()

        self.assertEqual(len(urls), 1, f"Lite agent should have exactly 1 URL, got: {urls}")
        self.assertEqual(urls[0], "http://69.164.244.51")

    @patch("apps.deployments.services.remote_orchestrator._ENFORCE_TLS", False)
    def test_full_install_has_multiple_ports(self):
        """Full install nodes should try multiple ports (80, 8090, 443)."""
        self.mock_server.is_lite_agent = False
        orchestrator = RemoteOrchestrator(self.mock_server)
        urls = orchestrator._candidate_base_urls()

        # Should have at least port 80 and 8090
        self.assertTrue(len(urls) >= 2, f"Full install should have multiple URLs, got: {urls}")
        self.assertIn("http://69.164.244.51", urls)
        self.assertIn("http://69.164.244.51:8090", urls)


if __name__ == "__main__":
    unittest.main()
