from asyncio import run

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.deployments.services.tunnels.tcp_server import TCPTunnelServer

from apps.deployments.models.tunnels import Tunnel

User = get_user_model()


@override_settings(TUNNEL_BASE_DOMAIN='tunnel.example.test')
class TunnelDomainConfigTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tunnel-user',
            email='tunnel@example.com',
            password='pass1234',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_dashboard_tunnel_create_uses_configured_tunnel_domain(self):
        response = self.client.post(
            reverse('tunnel-list'),
            {'local_port': 3000, 'subdomain': 'demo'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data['public_url'],
            'https://demo.tunnel.example.test',
        )
        self.assertEqual(
            Tunnel.objects.get(subdomain='demo').public_url,
            'https://demo.tunnel.example.test',
        )

    def test_cli_register_uses_configured_tunnel_domain(self):
        response = self.client.post(
            reverse('tunnel-register'),
            {'local_port': 8080, 'subdomain': 'cli-demo'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data['public_url'],
            'https://cli-demo.tunnel.example.test',
        )

    def test_tcp_tunnel_info_uses_configured_tunnel_domain(self):
        server = TCPTunnelServer()
        tunnel = run(server.create_tunnel(self.user.id, 5432))

        self.assertIsNotNone(tunnel)
        info = server.get_tunnel_info(tunnel.tunnel_id)

        self.assertIsNotNone(info)
        self.assertEqual(
            info['public_host'],
            f"tcp.tunnel.example.test:{tunnel.remote_port}",
        )
