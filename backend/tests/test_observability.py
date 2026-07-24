from unittest.mock import patch

from apps.deployments.models import Project, Service
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status

User = get_user_model()

class LokiQueryResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='password123'
        )
        self.project = Project.objects.create(name='Test Project', owner=self.user)

        # Non-COMPOSE (SINGLE) service
        self.single_svc = Service.objects.create(
            name='My Single Service',
            project=self.project,
            deploy_mode='SINGLE',
        )

        # COMPOSE service
        self.compose_svc = Service.objects.create(
            name='My Compose Service',
            project=self.project,
            deploy_mode='COMPOSE',
        )

        self.client.force_login(self.user)

    @patch('apps.core.views.observability.requests.get')
    def test_loki_query_resolves_uuid_with_operators(self, mock_get):
        # Mock responses from Loki
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            'status': 'success',
            'data': {
                'resultType': 'streams',
                'result': []
            }
        }

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.user)

        url = reverse('observability-loki-query')

        # Test cases for operators and deploy modes
        test_cases = [
            # (Service, Operator, Expected query substring)
            (self.single_svc, '=', 'compose_service="My Single Service"'),
            (self.single_svc, '=~', 'compose_service=~"My Single Service"'),
            (self.single_svc, '!=', 'compose_service!="My Single Service"'),
            (self.single_svc, '!~', 'compose_service!~"My Single Service"'),
            (self.compose_svc, '=', 'compose_project="my-compose-service"'),
            (self.compose_svc, '=~', 'compose_project=~"my-compose-service"'),
            (self.compose_svc, '!=', 'compose_project!="my-compose-service"'),
            (self.compose_svc, '!~', 'compose_project!~"my-compose-service"'),
        ]

        for svc, op, expected_substring in test_cases:
            query_param = f'{{compose_service{op}"{svc.id}"}}'
            response = client.get(url, {'query': query_param})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            # Verify the resolved query sent to Loki
            _called_args, called_kwargs = mock_get.call_args
            called_params = called_kwargs.get('params', {})
            self.assertIn(expected_substring, called_params.get('query', ''))
