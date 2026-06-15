from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


User = get_user_model()


class SubdomainHomoglyphRejectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="homoglyph-user", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_cyrillic_a_rejected(self):
        resp = self.client.post(
            "/api/v1/subdomains/",
            {"subdomain": "аdmin"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("disallowed", str(resp.data).lower())

    def test_cyrillic_o_rejected(self):
        resp = self.client.post(
            "/api/v1/subdomains/",
            {"subdomain": "rоot"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cyrillic_c_rejected(self):
        resp = self.client.post(
            "/api/v1/subdomains/",
            {"subdomain": "supportс"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_latin_only_accepted(self):
        resp = self.client.post(
            "/api/v1/subdomains/",
            {"subdomain": "myapp"},
            format="json",
        )
        self.assertIn(resp.status_code, (201, 400, 409))
        if resp.status_code == 400:
            self.assertNotIn("disallowed", str(resp.data).lower())
