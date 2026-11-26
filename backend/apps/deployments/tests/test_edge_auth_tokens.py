"""Regression tests for EdgeTokenService and EdgeAuthViewSet (aud binding & scope allowlist)."""
from __future__ import annotations

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.services.edge_tokens import EdgeTokenService

User = get_user_model()


class EdgeAuthAndTokensTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="edgeuser", email="edge@test.com", password="pw"
        )
        self.other = User.objects.create_user(
            username="edgeother", email="other@test.com", password="pw"
        )
        self.staff = User.objects.create_user(
            username="edgestaff", email="staff@test.com", password="pw", is_staff=True
        )
        self.project = Project.objects.create(owner=self.user, name="EdgeProj")
        self.service = Service.objects.create(
            name="edge-svc",
            owner=self.user,
            project=self.project,
            public_domain="app.example.com",
            custom_domains=["custom.example.com"],
        )
        self.other_service = Service.objects.create(
            name="other-svc",
            owner=self.other,
            public_domain="evil.example.com",
        )
        self.env_patch = patch.dict(os.environ, {"EDGE_JWT_SECRET": "unit-test-edge-secret-key"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_edge_token_service_aud_roundtrip_and_mismatch_rejection(self):
        tok = EdgeTokenService.mint(self.user, scope="service", ttl=300, aud="app.example.com:8443")
        res_ok = EdgeTokenService.verify(tok, aud="app.example.com:443")
        self.assertIsNotNone(res_ok)
        self.assertEqual(res_ok["user"].id, self.user.id)
        self.assertEqual(res_ok["aud"], "app.example.com")

        # Mismatched or missing host when aud is enforced must fail
        self.assertIsNone(EdgeTokenService.verify(tok, aud="other.example.com"))
        self.assertIsNone(EdgeTokenService.verify(tok, aud=""))

        # Unbound non-admin token must be rejected when aud is verified; unbound admin token is allowed
        unbound_tok = EdgeTokenService.mint(self.user, scope="preview", ttl=300)
        self.assertIsNone(EdgeTokenService.verify(unbound_tok, aud="any.example.com"))
        admin_tok = EdgeTokenService.mint(self.staff, scope="admin", ttl=300)
        res_admin = EdgeTokenService.verify(admin_tok, aud="any.example.com")
        self.assertIsNotNone(res_admin)
        self.assertEqual(res_admin["scope"], "admin")

    def test_mint_token_enforces_scope_allowlist_and_host_ownership(self):
        client = APIClient()
        client.force_authenticate(user=self.user)

        # Non-admin scope without service_id or aud must be rejected
        resp_unbound = client.post("/api/v1/edge/token/", {"scope": "preview"}, format="json")
        self.assertEqual(resp_unbound.status_code, 400)

        # Allowed scope bound to owned service succeeds
        resp_ok = client.post(
            "/api/v1/edge/token/",
            {"scope": "preview", "service_id": str(self.service.id)},
            format="json",
        )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertEqual(resp_ok.data["scope"], "preview")
        self.assertEqual(resp_ok.data["aud"], "app.example.com")

        # Non-allowlisted scope rejected for regular user
        resp_bad_scope = client.post("/api/v1/edge/token/", {"scope": "admin"}, format="json")
        self.assertEqual(resp_bad_scope.status_code, 403)

        # Staff user can mint privileged scope
        staff_client = APIClient()
        staff_client.force_authenticate(user=self.staff)
        resp_staff = staff_client.post("/api/v1/edge/token/", {"scope": "admin"}, format="json")
        self.assertEqual(resp_staff.status_code, 200)
        self.assertEqual(resp_staff.data["scope"], "admin")

        # Binding to owned service / custom domain succeeds
        resp_bound = client.post(
            "/api/v1/edge/token/",
            {"scope": "service", "service_id": str(self.service.id), "aud": "custom.example.com"},
            format="json",
        )
        self.assertEqual(resp_bound.status_code, 200)
        self.assertEqual(resp_bound.data["aud"], "custom.example.com")
        bound_tok = resp_bound.data["token"]

        # Binding to another user's host or service is rejected
        resp_foreign_host = client.post(
            "/api/v1/edge/token/",
            {"scope": "service", "aud": "evil.example.com"},
            format="json",
        )
        self.assertEqual(resp_foreign_host.status_code, 403)

        resp_foreign_svc = client.post(
            "/api/v1/edge/token/",
            {"scope": "service", "service_id": str(self.other_service.id)},
            format="json",
        )
        self.assertEqual(resp_foreign_svc.status_code, 403)

        # auth-verify enforces X-Forwarded-Host against aud
        anon = APIClient()
        v_ok = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION=f"Bearer {bound_tok}",
            HTTP_X_FORWARDED_HOST="custom.example.com:443",
        )
        self.assertEqual(v_ok.status_code, 200)
        self.assertEqual(v_ok["X-User-Id"], str(self.user.id))
        self.assertEqual(v_ok["X-Edge-Scope"], "service")

        v_bad_host = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION=f"Bearer {bound_tok}",
            HTTP_X_FORWARDED_HOST="evil.example.com",
        )
        self.assertEqual(v_bad_host.status_code, 401)

        v_no_host = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION=f"Bearer {bound_tok}",
        )
        self.assertEqual(v_no_host.status_code, 401)

        # Unbound non-admin token rejected on auth-verify; unbound admin token accepted
        unbound_non_admin = EdgeTokenService.mint(self.user, scope="preview", ttl=300)
        v_unbound = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION=f"Bearer {unbound_non_admin}",
            HTTP_X_FORWARDED_HOST="app.example.com",
        )
        self.assertEqual(v_unbound.status_code, 401)

        v_admin = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION=f"Bearer {resp_staff.data['token']}",
            HTTP_X_FORWARDED_HOST="app.example.com",
        )
        self.assertEqual(v_admin.status_code, 200)
        self.assertEqual(v_admin["X-Edge-Scope"], "admin")

        # Oversized Authorization header rejected
        v_huge = anon.get(
            "/api/v1/edge/auth-verify/",
            HTTP_AUTHORIZATION="Bearer " + ("a" * 9000),
        )
        self.assertEqual(v_huge.status_code, 401)
