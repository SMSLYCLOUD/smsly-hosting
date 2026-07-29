from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer

User = get_user_model()


def _always_valid_hmac(*args, **kwargs):
    return (True, "")


class Finding38ElectionMeshMultiplicityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='f38-user', password='p',
        )
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name='f38-server',
            host='203.0.113.50',
            wg_address='10.100.0.50',
            api_url='https://203.0.113.50',
            gateway_secret='f38-secret-1234567890',
        )

        self.mesh_alpha = MeshNetwork.objects.create(
            name='alpha-38', is_active=True,
        )
        self.mesh_beta = MeshNetwork.objects.create(
            name='beta-38', is_active=True,
        )
        self.mesh_gamma = MeshNetwork.objects.create(
            name='gamma-38', is_active=True,
        )

        WireGuardPeer.objects.create(
            mesh=self.mesh_alpha, server=self.server,
            private_key='k1', public_key='pub-alpha',
            wg_address='10.100.0.50', is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_beta, server=self.server,
            private_key='k2', public_key='pub-beta',
            wg_address='10.100.0.50', is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_gamma, server=self.server,
            private_key='k3', public_key='pub-gamma',
            wg_address='10.100.0.50', is_active=True,
        )

    def _post(self, url, data):
        import json
        client = APIClient()
        client.force_authenticate(user=self.user)
        body = json.dumps(data).encode()
        return client.post(url, data=body, content_type='application/json')

    def test_vote_request_rejects_when_peer_in_multiple_active_meshes(self):
        with patch(
            'apps.core.views.election._verify_election_hmac',
            side_effect=_always_valid_hmac,
        ):
            resp = self._post(
                '/api/v1/internal/vote/',
                data={'term': 1, 'candidate_wg_address': '10.100.0.50'},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertIn('Ambiguous', str(resp.data))

    def test_vote_request_rejects_when_peer_in_multiple_active_meshes_via_wg_address(self):
        WireGuardPeer.objects.filter(server=self.server).delete()
        MeshNetwork.objects.filter(is_active=True).delete()
        m1 = MeshNetwork.objects.create(name='m1', is_active=True)
        m2 = MeshNetwork.objects.create(name='m2', is_active=True)
        WireGuardPeer.objects.create(
            mesh=m1, wg_address='10.100.0.60', public_key='p1',
            private_key='sk1', is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=m2, wg_address='10.100.0.60', public_key='p2',
            private_key='sk2', is_active=True,
        )
        with patch(
            'apps.core.views.election._verify_election_hmac',
            side_effect=_always_valid_hmac,
        ):
            resp = self._post(
                '/api/v1/internal/vote/',
                data={'term': 1, 'candidate_wg_address': '10.100.0.60'},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertIn('Ambiguous', str(resp.data))

    def test_heartbeat_does_not_silently_route_when_peer_in_multiple_meshes(self):
        with patch(
            'apps.core.views.election._verify_election_hmac',
            side_effect=_always_valid_hmac,
        ), patch(
            'apps.deployments.services.election_service.'
            'ElectionService.receive_heartbeat'
        ) as mock_recv:
            resp = self._post(
                '/api/v1/internal/heartbeat/',
                data={'term': 1, 'leader_wg_address': '10.100.0.50'},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.data.get('accepted'), None)
        mock_recv.assert_not_called()
