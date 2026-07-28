"""Notification API security and ownership tests."""

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification, NotificationPreference


class NotificationApiSecurityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='notif-user',
            email='notif-user@example.com',
            password='password123',
        )
        self.other_user = User.objects.create_user(
            username='notif-other',
            email='notif-other@example.com',
            password='password123',
        )
        self.client.force_authenticate(user=self.user)

    def test_notifications_endpoint_disallows_create(self):
        response = self.client.post(
            '/api/v1/notifications/',
            {
                'user': self.other_user.id,
                'title': 'Injected',
                'message': 'Should be blocked',
                'event_type': 'deploy_failed',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(Notification.objects.filter(title='Injected').exists())

    def test_preferences_create_forces_authenticated_user(self):
        response = self.client.post(
            '/api/v1/preferences/',
            {
                'user': self.other_user.id,
                'event_type': 'deploy_success',
                'channels': ['email'],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        pref = NotificationPreference.objects.get(id=response.data['id'])
        self.assertEqual(pref.user_id, self.user.id)

    def test_preferences_patch_cannot_reassign_user(self):
        pref = NotificationPreference.objects.create(
            user=self.user,
            event_type='deploy_failed',
            channels=['email'],
        )
        response = self.client.patch(
            f'/api/v1/preferences/{pref.id}/',
            {'user': self.other_user.id, 'channels': ['in_app']},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        pref.refresh_from_db()
        self.assertEqual(pref.user_id, self.user.id)
        self.assertEqual(pref.channels, ['in_app'])
