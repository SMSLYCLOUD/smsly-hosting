# pylint: disable=invalid-name
"""Tests for the ``generate_iac`` disclaimer (Issue 28).

The endpoint generates a Terraform template from a natural-language
description.  The templates lack ``required_providers`` blocks,
variables, and backend config.  Users may deploy these directly to
their cloud account, leaking credentials via the embedded
``db_admin`` / ``db_password`` variables.  To make the preview
state unambiguous the response now includes a ``disclaimer``
field with explicit guidance.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


User = get_user_model()


DISCLAIMER_TEXT = (
    "This is a preview template. Review required_providers, "
    "variables, and backend config before deploy."
)


class GenerateIacDisclaimerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="iac-disclaimer-user",
            email="iac-disclaimer@test.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_response_includes_disclaimer_field(self):
        response = self.client.post(
            "/api/v1/cloud/intelligence/generate_iac/",
            {"description": "create postgres database"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("disclaimer", response.data)
        self.assertEqual(response.data["disclaimer"], DISCLAIMER_TEXT)

    def test_disclaimer_present_for_aws_bucket_template(self):
        response = self.client.post(
            "/api/v1/cloud/intelligence/generate_iac/",
            {"description": "s3 bucket for static assets", "cloud": "aws"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("disclaimer", response.data)
        self.assertEqual(response.data["disclaimer"], DISCLAIMER_TEXT)

    def test_disclaimer_present_for_gcp_kubernetes_template(self):
        response = self.client.post(
            "/api/v1/cloud/intelligence/generate_iac/",
            {"description": "kubernetes cluster", "cloud": "gcp"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("disclaimer", response.data)
        self.assertEqual(response.data["disclaimer"], DISCLAIMER_TEXT)
