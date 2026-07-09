"""Regression tests: per-registry ownership check on server.attach registries.

Before the fix in ``views_servers.py::registries`` (POST handler), any
authenticated user could POST a list of active ScopedRegistry UUIDs and
attach them to their own server. The node installer would then run
``docker login`` with credentials belonging to a different tenant.

The fix filters the input list down to registries whose GenericForeignKey
scope (Organization / Team / Project) is one the requesting user has a
relationship with:

  * any Organization the user is a member of (any role)
  * any Team where the user has an active TeamMember record OR owns the Team
  * any Project the user owns

The error response collapses "missing / inactive / inaccessible" into one
opaque 400 to avoid enumeration via probing.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import ScopedRegistry
from apps.deployments.models_core import Project
from apps.deployments.models_servers import ManagedServer
from apps.organizations.models import Organization, OrganizationMembership
from apps.teams.models import Team

User = get_user_model()


class ServerRegistriesAttachScopeTests(TestCase):
    """POST /api/v1/servers/{id}/registries/ must respect scope ownership."""

    def setUp(self):
        # Two tenants: attacker + victim. Each has an org, a team, a project,
        # and a server. The victim has a ScopedRegistry on every level.
        self.attacker = User.objects.create_user(
            username="attacker", password="pw",
        )
        self.victim = User.objects.create_user(
            username="victim", password="pw",
        )

        self.attacker_org = Organization.objects.create(
            owner=self.attacker, name="attacker-org", slug="attacker-org",
        )
        self.victim_org = Organization.objects.create(
            owner=self.victim, name="victim-org", slug="victim-org",
        )

        self.attacker_team = Team.objects.create(
            owner=self.attacker, name="attacker-team",
            organization=self.attacker_org,
        )
        self.victim_team = Team.objects.create(
            owner=self.victim, name="victim-team",
            organization=self.victim_org,
        )

        self.attacker_project = Project.objects.create(
            owner=self.attacker, name="attacker-project",
            slug="attacker-project", team=self.attacker_team,
        )
        self.victim_project = Project.objects.create(
            owner=self.victim, name="victim-project",
            slug="victim-project", team=self.victim_team,
        )

        # The attacker has their own memberships so the test exercises the
        # *positive* path too.
        OrganizationMembership.objects.create(
            organization=self.attacker_org, user=self.attacker,
            role=OrganizationMembership.Role.OWNER,
        )

        # ScopedRegistry records the victim owns — none of these should be
        # attachable by the attacker.
        self.victim_org_registry = ScopedRegistry.objects.create(
            content_type=ContentType.objects.get_for_model(Organization),
            object_id=self.victim_org.id,
            registry_url="victim-org.example.com:5000",
            username="victim",
            password="victim-pw",
        )
        self.victim_team_registry = ScopedRegistry.objects.create(
            content_type=ContentType.objects.get_for_model(Team),
            object_id=self.victim_team.id,
            registry_url="victim-team.example.com:5000",
            username="victim",
            password="victim-pw",
        )
        self.victim_project_registry = ScopedRegistry.objects.create(
            content_type=ContentType.objects.get_for_model(Project),
            object_id=self.victim_project.id,
            registry_url="victim-project.example.com:5000",
            username="victim",
            password="victim-pw",
        )

        # A registry the attacker legitimately owns — should remain attachable.
        self.attacker_org_registry = ScopedRegistry.objects.create(
            content_type=ContentType.objects.get_for_model(Organization),
            object_id=self.attacker_org.id,
            registry_url="attacker-org.example.com:5000",
            username="attacker",
            password="attacker-pw",
        )

        self.attacker_server = ManagedServer.objects.create(
            owner=self.attacker,
            name="attacker-server",
            host="10.0.0.10",
            api_url="https://attacker-server.example.com",
            api_token="tok",
        )

        self.client_api = APIClient()
        self.client_api.force_authenticate(user=self.attacker)

    def _attach(self, registry_ids):
        return self.client_api.post(
            f"/api/v1/servers/{self.attacker_server.id}/registries/",
            {"registry_ids": [str(rid) for rid in registry_ids]},
            format="json",
        )

    # ── Negative paths ─────────────────────────────────────────────────

    def test_attacker_cannot_attach_victim_org_registry(self):
        resp = self._attach([self.victim_org_registry.id])
        self.assertEqual(resp.status_code, 400)
        self.assertIn(
            "invalid, inactive, or inaccessible",
            resp.data["error"],
        )
        self.assertFalse(
            self.attacker_server.registry_access.filter(
                id=self.victim_org_registry.id,
            ).exists(),
            "victim org registry must not be attached",
        )

    def test_attacker_cannot_attach_victim_team_registry(self):
        resp = self._attach([self.victim_team_registry.id])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            self.attacker_server.registry_access.filter(
                id=self.victim_team_registry.id,
            ).exists(),
        )

    def test_attacker_cannot_attach_victim_project_registry(self):
        resp = self._attach([self.victim_project_registry.id])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            self.attacker_server.registry_access.filter(
                id=self.victim_project_registry.id,
            ).exists(),
        )

    def test_mixed_list_with_one_victim_registry_is_rejected(self):
        """Even if one ID is legitimately owned, a single victim ID in the
        same request must cause the whole request to fail rather than being
        silently attached.
        """
        resp = self._attach([
            self.attacker_org_registry.id,
            self.victim_org_registry.id,
        ])
        self.assertEqual(resp.status_code, 400)
        # Neither should be attached — the request failed atomically.
        self.assertFalse(self.attacker_server.registry_access.exists())

    # ── Positive paths ─────────────────────────────────────────────────

    def test_attacker_can_attach_own_org_registry(self):
        resp = self._attach([self.attacker_org_registry.id])
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(
            self.attacker_server.registry_access.filter(
                id=self.attacker_org_registry.id,
            ).exists(),
        )

    # ── Opaque error: don't leak which IDs exist ──────────────────────

    def test_inaccessible_and_nonexistent_share_the_same_error(self):
        """The user must not be able to distinguish 'does not exist' from
        'exists but you cannot access it'."""
        import uuid as _uuid

        nonexistent = _uuid.uuid4()
        resp_inexistent = self._attach([nonexistent])
        resp_victim = self._attach([self.victim_org_registry.id])
        self.assertEqual(
            resp_inexistent.status_code, resp_victim.status_code,
        )
        self.assertEqual(
            resp_inexistent.data["error"], resp_victim.data["error"],
        )
