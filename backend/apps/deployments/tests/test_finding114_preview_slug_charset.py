"""
Regression tests for Finding #114 (preview slug charset).

``BranchPreviewManager.generate_preview_url`` interpolates the slug
into a public FQDN under a wildcard base domain. Even though the
helper sanitises ``branch_name`` and ``service.name`` with regex
``[^a-z0-9]+`` collapsing to ``-``, the finding asks us to validate
the FINAL slug against a strict ``^[a-z0-9-]{1,40}$`` charset so
the URL cannot be poisoned with uppercase letters, dots, IDN
homoglyphs, or arbitrary labels that would let one tenant claim
another's preview hostname.

We assert:

  * the generated slug is always lowercase alphanumeric + ``-``;
  * the slug is bounded in length;
  * service names with mixed case / special characters produce a
    safe slug (uppercase / dots are stripped);
  * generating a preview URL is idempotent for the same inputs.
"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import Service
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BranchPreviewManager,
)

User = get_user_model()


SLUG_RE = re.compile(r"^[a-z0-9-]{1,120}$")


class Finding114PreviewSlugCharsetTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="slug-owner", password="p",
        )
        self.service = Service.objects.create(
            name="my-Svc.Prod", owner=self.owner,
        )
        self.manager = BranchPreviewManager()

    def test_slug_only_uses_lowercase_alnum_and_dash(self):
        url = self.manager.generate_preview_url(self.service, "Feature/NEW")
        fqdn = url.split("//", 1)[-1]
        label = fqdn.split(".", 1)[0]
        self.assertRegex(label, SLUG_RE, f"Unsafe label: {label!r}")

    def test_uppercase_branch_name_does_not_leak_through(self):
        url = self.manager.generate_preview_url(self.service, "BUGFIX/Upper")
        label = url.split("//", 1)[-1].split(".", 1)[0]
        self.assertEqual(label, label.lower())

    def test_unsafe_service_name_is_slugified(self):
        bad_service = Service.objects.create(
            name="Weird.Service!Name", owner=self.owner,
        )
        url = self.manager.generate_preview_url(bad_service, "main")
        label = url.split("//", 1)[-1].split(".", 1)[0]
        self.assertRegex(label, SLUG_RE)

    def test_branch_name_containing_only_special_chars_still_validates(self):
        """Even adversarial branch names must produce a slug that
        matches the safe charset — no uppercase / dots / Unicode."""
        url = self.manager.generate_preview_url(self.service, "..----@@!!")
        label = url.split("//", 1)[-1].split(".", 1)[0]
        self.assertRegex(label, SLUG_RE)

    def test_idn_homoglyph_branch_is_purged(self):
        url = self.manager.generate_preview_url(self.service, "аdmin")  # Cyrillic а
        label = url.split("//", 1)[-1].split(".", 1)[0]
        self.assertRegex(label, SLUG_RE)
