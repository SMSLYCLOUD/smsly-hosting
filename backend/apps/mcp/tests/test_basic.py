from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.mcp.tools import _resolve_user, list_services, get_deployment_status

User = get_user_model()


class ResolveUserTests(TestCase):
    def test_resolve_user_returns_none_when_no_args(self):
        result = _resolve_user()
        self.assertIsNone(result)

    def test_resolve_user_by_id(self):
        user = User.objects.create_user(email="test@example.com", password="pass")
        result = _resolve_user(user_id=str(user.id))
        self.assertEqual(result, user)

    def test_resolve_user_by_email(self):
        user = User.objects.create_user(email="test@example.com", password="pass")
        result = _resolve_user(user_email="test@example.com")
        self.assertEqual(result, user)

    def test_resolve_user_not_found_raises(self):
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            _resolve_user(user_id="00000000-0000-0000-0000-000000000000")


class ListServicesTests(TestCase):
    def test_list_services_empty(self):
        result = list_services()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)
