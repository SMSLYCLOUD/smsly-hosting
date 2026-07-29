from django.test import TestCase

from apps.licensing.models import PlatformLicense, PlatformTier


class PlatformLicenseTests(TestCase):
    def test_create_license(self):
        lic = PlatformLicense.objects.create(
            license_key="test-key-123",
            tier=PlatformTier.PRO,
            max_services=10,
            max_team_members=5,
        )
        self.assertIsNotNone(lic.pk)
        self.assertEqual(lic.tier, "pro")

    def test_str(self):
        lic = PlatformLicense.objects.create(tier=PlatformTier.COMMUNITY)
        result = str(lic)
        self.assertIn("Platform License", result)

    def test_load_creates_singleton(self):
        PlatformLicense.objects.all().delete()
        lic = PlatformLicense.load()
        self.assertEqual(lic.pk, 1)
        self.assertEqual(lic.tier, PlatformTier.COMMUNITY)
        self.assertEqual(PlatformLicense.objects.count(), 1)

    def test_tier_properties(self):
        comm = PlatformLicense(tier=PlatformTier.COMMUNITY)
        self.assertTrue(comm.is_community)
        self.assertFalse(comm.is_pro)
        self.assertFalse(comm.is_enterprise)

        pro = PlatformLicense(tier=PlatformTier.PRO)
        self.assertFalse(pro.is_community)
        self.assertTrue(pro.is_pro)
        self.assertFalse(pro.is_enterprise)

        ent = PlatformLicense(tier=PlatformTier.ENTERPRISE)
        self.assertFalse(ent.is_community)
        self.assertTrue(ent.is_pro)
        self.assertTrue(ent.is_enterprise)
