# pylint: disable=invalid-name
from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APITestCase


class BillingSubscribeAliasTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('bill-user', 'bill@example.com', 'password123')
        self.client.force_authenticate(user=self.user)

    @patch('apps.billing.views.CheckoutView.post')
    def test_subscribe_alias_delegates_to_checkout_view(self, checkout_post_mock):
        checkout_post_mock.return_value = Response({'url': 'https://checkout.example/session'})

        response = self.client.post(
            reverse('subscription-subscribe'),
            {'plan': 'PRO', 'provider': 'stripe'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['url'], 'https://checkout.example/session')
        checkout_post_mock.assert_called_once()


class CloudIntelligenceChatTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('cloud-user', 'cloud@example.com', 'password123')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('intelligence-chat')

    @patch('apps.cloud.views.ask_with_fallback')
    def test_chat_returns_provider_name(self, ask_mock):
        ask_mock.return_value = ('ok-response', 'mock-provider')

        response = self.client.post(self.url, {'message': 'help me debug deploy'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['response'], 'ok-response')
        self.assertEqual(response.data['provider'], 'mock-provider')
        ask_mock.assert_called_once()

    def test_chat_rejects_empty_message(self):
        response = self.client.post(self.url, {'message': '   '}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
