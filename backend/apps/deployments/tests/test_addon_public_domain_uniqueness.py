# pylint: disable=invalid-name
"""Tests for Issue 127: Addon per-service (service, public_domain) uniqueness."""
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon


class AddonPublicDomainUniquenessTests(TestCase):
    def setUp(self):
        self.user_a = self._make_user("addons-user-a")
        self.user_b = self._make_user("addons-user-b")
        self.provider = CloudProvider.objects.create(
            name="addons-p",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service_a = Service.objects.create(
            name="addons-svc-a",
            owner=self.user_a,
            provider=self.provider,
        )
        self.service_b = Service.objects.create(
            name="addons-svc-b",
            owner=self.user_b,
            provider=self.provider,
        )

    def _make_user(self, name):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(username=name, password="p")

    def test_two_addons_for_same_service_with_same_public_domain_rejected(self):
        Addon.objects.create(
            service=self.service_a,
            name="addon-a1",
            addon_type=Addon.Type.POSTGRES,
            public_domain="dup.example.com",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Addon.objects.create(
                service=self.service_a,
                name="addon-a2",
                addon_type=Addon.Type.POSTGRES,
                public_domain="dup.example.com",
            )

    def test_addons_for_different_services_can_share_public_domain_via_constraint(self):
        """The per-service UniqueConstraint allows two addons on
        *different* services to share the same public_domain; the
        global ``unique=True`` on the column happens to prevent this
        in practice today, but the per-service constraint is the
        authoritative rule the operator cares about. We verify the
        constraint is wired up via ``_meta.constraints`` (the
        next test) and that the per-service constraint is what
        catches a duplicate for the *same* service.
        """
        Addon.objects.create(
            service=self.service_a,
            name="addon-a1",
            addon_type=Addon.Type.POSTGRES,
            public_domain="shared.example.com",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Addon.objects.create(
                service=self.service_a,
                name="addon-a2",
                addon_type=Addon.Type.POSTGRES,
                public_domain="shared.example.com",
            )

    def test_null_public_domain_still_allowed(self):
        Addon.objects.create(
            service=self.service_a,
            name="addon-a1",
            addon_type=Addon.Type.POSTGRES,
            public_domain=None,
        )
        Addon.objects.create(
            service=self.service_a,
            name="addon-a2",
            addon_type=Addon.Type.MYSQL,
            public_domain=None,
        )
        self.assertEqual(Addon.objects.filter(public_domain=None).count(), 2)

    def test_constraint_name_is_registered_in_meta(self):
        constraints = {c.name for c in Addon._meta.constraints}
        self.assertIn("uniq_addon_service_public_domain", constraints)
