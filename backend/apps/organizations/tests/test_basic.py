from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.organizations.models import Organization, OrganizationMembership

User = get_user_model()


class OrganizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="owner@example.com", password="pass")

    def test_create_organization(self):
        org = Organization.objects.create(
            name="Acme Corp",
            slug="acme-corp",
            owner=self.user,
        )
        self.assertIsNotNone(org.pk)
        self.assertEqual(org.name, "Acme Corp")

    def test_str(self):
        org = Organization.objects.create(
            name="Acme Corp",
            slug="acme-corp",
            owner=self.user,
        )
        self.assertEqual(str(org), "Acme Corp")

    def test_slug_unique(self):
        Organization.objects.create(name="Org1", slug="dup", owner=self.user)
        with self.assertRaises(Exception):
            Organization.objects.create(name="Org2", slug="dup", owner=self.user)


class OrganizationMembershipTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="member@example.com", password="pass")
        self.org = Organization.objects.create(
            name="Test Org",
            slug="test-org",
            owner=self.user,
        )

    def test_create_membership(self):
        membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.user,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.assertIsNotNone(membership.pk)
        self.assertEqual(membership.role, "ADMIN")

    def test_str(self):
        membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.user,
            role=OrganizationMembership.Role.MEMBER,
        )
        result = str(membership)
        self.assertIn("MEMBER", result)
        self.assertIn("Test Org", result)

    def test_default_role_is_member(self):
        membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.user,
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.MEMBER)
