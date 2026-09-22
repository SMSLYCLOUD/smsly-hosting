"""Service move across projects + membership inheritance.

Joining a project must give the service the project's identity:
ecosystem trust/sidecar in ecosystem projects, platform trust in
plain ones. Covers the service-side move-project action, the
project-side move-service/remove-service actions, and creation
with a project (all funnel through the same helper).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Service
from apps.deployments.models.core import Project
from apps.deployments.services.project_membership import (
    apply_project_membership,
    project_flavor,
)
from apps.mtls.models import MtlsConfig

User = get_user_model()


class ProjectMoveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="move-user", password="p", email="m@e.com")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.plain = Project.objects.create(owner=self.user, name="plain")
        self.eco = Project.objects.create(owner=self.user, name="eco")
        # Ecosystem flavor via a managed sibling with sidecar on.
        # (A creation signal already makes the MtlsConfig row.)
        self.sibling = Service.objects.create(
            name="eco-sib", owner=self.user, project=self.eco,
            managed_by="ECOSYSTEM",
        )
        MtlsConfig.objects.update_or_create(
            service=self.sibling,
            defaults={"enabled": True, "trust_domain": "ecosystem.local",
                      "sidecar_enabled": True})
        self.svc = Service.objects.create(
            name="mover", owner=self.user, project=self.plain)

    def _trust(self, service):
        return MtlsConfig.objects.get(service=service)

    def test_flavor_detection(self):
        self.assertEqual(project_flavor(self.eco), "ecosystem")
        self.assertEqual(project_flavor(self.plain), "platform")
        self.assertEqual(project_flavor(None), "platform")

    def test_move_into_ecosystem_inherits_mtls(self):
        resp = self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": str(self.eco.id)}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.svc.refresh_from_db()
        self.assertEqual(str(self.svc.project_id), str(self.eco.id))
        cfg = self._trust(self.svc)
        self.assertEqual(cfg.trust_domain, "ecosystem.local")
        self.assertTrue(cfg.sidecar_enabled)
        self.assertIn("Redeploy", resp.data.get("message", ""))

    def test_move_into_platform_normalizes_trust(self):
        eco_svc = Service.objects.create(
            name="eco-leaver", owner=self.user, project=self.eco)
        MtlsConfig.objects.update_or_create(
            service=eco_svc,
            defaults={"enabled": True, "trust_domain": "ecosystem.local",
                      "sidecar_enabled": True})
        resp = self.client.post(
            f"/api/v1/services/{eco_svc.id}/move-project/",
            {"project_id": str(self.plain.id)}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        cfg = self._trust(eco_svc)
        self.assertEqual(cfg.trust_domain, "platform.local")

    def test_move_requires_target(self):
        resp = self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": None}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_move_same_project_no_change(self):
        resp = self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": str(self.plain.id)}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("status"), "no_change")

    def test_move_unknown_project_404(self):
        import uuid
        resp = self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_move_into_foreign_project_forbidden(self):
        other = User.objects.create_user(
            username="other", password="p", email="o@e.com")
        foreign = Project.objects.create(owner=other, name="foreign")
        resp = self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": str(foreign.id)}, format="json")
        self.assertIn(resp.status_code, (403, 404))
        self.svc.refresh_from_db()
        self.assertEqual(str(self.svc.project_id), str(self.plain.id))

    def test_project_side_move_service_inherits(self):
        resp = self.client.post(
            f"/api/v1/projects/{self.eco.id}/move-service/",
            {"service_id": str(self.svc.id)}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        cfg = self._trust(self.svc)
        self.assertEqual(cfg.trust_domain, "ecosystem.local")
        self.assertTrue(cfg.sidecar_enabled)

    def test_remove_service_replacement_inherits(self):
        resp = self.client.post(
            f"/api/v1/projects/{self.plain.id}/remove-service/",
            {"service_id": str(self.svc.id),
             "replacement_project_id": str(self.eco.id)},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.svc.refresh_from_db()
        self.assertEqual(str(self.svc.project_id), str(self.eco.id))
        cfg = self._trust(self.svc)
        self.assertEqual(cfg.trust_domain, "ecosystem.local")

    def test_addons_follow_the_service(self):
        from apps.deployments.models.addons import Addon
        addon = Addon.objects.create(
            service=self.svc, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, project=self.plain)
        self.client.post(
            f"/api/v1/services/{self.svc.id}/move-project/",
            {"project_id": str(self.eco.id)}, format="json")
        addon.refresh_from_db()
        self.assertEqual(str(addon.project_id), str(self.eco.id))

    def test_helper_matches_sibling_posture_off(self):
        MtlsConfig.objects.filter(service=self.sibling).update(
            sidecar_enabled=False, enabled=False)
        result = apply_project_membership(self.svc, self.eco)
        self.assertEqual(result["flavor"], "ecosystem")
        self.assertFalse(result["sidecar_enabled"])
        cfg = self._trust(self.svc)
        self.assertEqual(cfg.trust_domain, "ecosystem.local")
        self.assertFalse(cfg.sidecar_enabled)
