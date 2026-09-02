"""Regression tests for the hardened SSRF validator.

Attack surface: tenant-controlled webhook URLs are fetched by the
backend container. The OLD validator checked literal strings only —
three bypasses worked:

  1. DNS BYPASS — 'internal.evil.com' resolves to 169.254.169.254;
     the literal never matched the blocklist, the fetch hit cloud
     metadata.
  2. ROUND-ROBIN REBINDING — a DNS name with one public + one private
     A record; the fetcher could connect to either. Now EVERY resolved
     address must be public.
  3. DOCKER-ALIAS BYPASS — hostname 'backend' resolves INSIDE the
     dispatcher container to the control plane itself; combined with
     the zero-trust exempt paths (webhook receivers), the tenant could
     forge service-creation events through us.
  4. INTEGER-IP ENCODING — http://2130706433/ is 127.0.0.1.
  5. SELF-TARGETING — a webhook pointing at our own public hostname
     plus /api/v1/webhooks/github/ reaches the unauthenticated GitHub
     receiver.
"""
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.core.validators import validate_ssrf


def _resolve_to(addrs):
    """getaddrinfo side effect returning the given addresses."""
    infos = []
    for a in addrs:
        family = 2 if ':' not in a else 10
        infos.append((family, None, None, '', (a, 0)))
    return infos


class LiteralChecks(SimpleTestCase):
    def test_paths_without_host_pass(self):
        validate_ssrf('/health')
        validate_ssrf('/api/v1/status')

    def test_public_https_passes(self):
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['93.184.216.34'])):
            validate_ssrf('https://api.github.com/status')

    def test_localhost_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('http://localhost:8080/health')

    def test_literal_private_ip_blocked(self):
        for url in ('http://10.0.0.5/internal', 'http://192.168.1.100/x',
                    'http://172.16.0.5/metrics', 'http://169.254.169.254/latest/'):
            with self.assertRaises(ValidationError, msg=url):
                validate_ssrf(url)

    def test_ipv6_loopback_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('http://[::1]/admin')

    def test_integer_encoded_loopback_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('http://2130706433/admin')

    def test_docker_alias_backend_blocked(self):
        # 'backend' resolves inside the container to the control plane.
        with self.assertRaises(ValidationError):
            validate_ssrf('http://backend:8000/api/v1/')

    def test_docker_alias_postgres_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('http://postgres:5432/')

    def test_metadata_hostnames_blocked(self):
        for h in ('metadata.google.internal', 'metadata.goog'):
            with self.assertRaises(ValidationError, msg=h):
                validate_ssrf(f'http://{h}/computeMetadata/v1/')


class DnsResolutionChecks(SimpleTestCase):
    def test_hostname_resolving_to_private_blocked(self):
        # DNS BYPASS: the literal is an innocent-looking domain, but it
        # resolves into the link-local metadata range.
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['169.254.169.254'])):
            with self.assertRaises(ValidationError):
                validate_ssrf('https://internal.evil.com/token')

    def test_hostname_resolving_to_rfc1918_blocked(self):
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['10.2.3.4'])):
            with self.assertRaises(ValidationError):
                validate_ssrf('https://intranet.evil.com/')

    def test_round_robin_mixed_records_blocked(self):
        # One public + one private record: the OLD validator passed
        # (string clean); the fetcher could connect to the private one.
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['93.184.216.34', '10.0.0.9'])):
            with self.assertRaises(ValidationError):
                validate_ssrf('https://dual.evil.com/hook')

    def test_all_public_resolves_pass(self):
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['93.184.216.34', '104.16.132.229'])):
            validate_ssrf('https://multi.good.com/hook')

    def test_unresolvable_hostname_passes_validation(self):
        # Validation-time leniency: a name may not resolve NOW (private
        # DNS, future endpoint). The dangerous case — resolves to
        # private — is caught above; NXDOMAIN here lets the operator
        # save the config and the fetch simply fails later.
        import socket
        with mock.patch('socket.getaddrinfo', side_effect=socket.gaierror('nx')):
            validate_ssrf('https://not-resolvable.example.com/hook')


class SelfTargetingChecks(SimpleTestCase):
    def test_our_public_host_plus_exempt_path_blocked(self):
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['93.184.216.34'])):
            with self.assertRaises(ValidationError):
                validate_ssrf('https://grid.smsly.cloud/api/v1/webhooks/github/')

    def test_exempt_service_webhook_path_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('https://anything.example/api/v1/services/webhook/github/')

    def test_node_token_exchange_blocked(self):
        with self.assertRaises(ValidationError):
            validate_ssrf('https://example.com/api/v1/auth/node-token-exchange')

    def test_regular_api_path_on_public_host_allowed(self):
        # Normal API paths behind zero-trust auth are not special —
        # only the exempt (unauthenticated) surface is deniedlisted.
        with mock.patch('socket.getaddrinfo', side_effect=lambda *a, **k: _resolve_to(['93.184.216.34'])):
            validate_ssrf('https://grid.smsly.cloud/api/v1/services/')
