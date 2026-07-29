"""Tests for permissions RBAC system."""
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.permissions.codes import ADMIN_ACCESS, BILLING_MANAGE, BILLING_VIEW
from apps.permissions.utils import has_permission

User = get_user_model()


class HasPermissionUtilTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='perm-tester', email='perm@test.com', password='pass123'
        )
        self.admin = User.objects.create_superuser(
            username='perm-admin', email='perm-admin@test.com', password='pass123'
        )

    def test_superuser_always_has_permission(self):
        result = has_permission(self.admin, MagicMock(), ADMIN_ACCESS)
        self.assertTrue(result)

    @patch('apps.permissions.utils._get_user_permissions')
    def test_user_with_permission_code(self, mock_perms):
        mock_perms.return_value = {BILLING_VIEW}
        result = has_permission(self.user, MagicMock(), BILLING_VIEW)
        self.assertTrue(result)

    @patch('apps.permissions.utils._get_user_permissions')
    def test_user_without_permission_code(self, mock_perms):
        mock_perms.return_value = set()
        result = has_permission(self.user, MagicMock(), BILLING_MANAGE)
        self.assertFalse(result)


class PermissionCodesTests(TestCase):
    def test_codes_are_strings(self):
        self.assertIsInstance(ADMIN_ACCESS, str)
        self.assertIsInstance(BILLING_MANAGE, str)
        self.assertIsInstance(BILLING_VIEW, str)

    def test_codes_are_not_empty(self):
        self.assertTrue(len(ADMIN_ACCESS) > 0)
        self.assertTrue(len(BILLING_MANAGE) > 0)
        self.assertTrue(len(BILLING_VIEW) > 0)
