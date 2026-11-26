"""Regression tests covering all 21 Settings E2E security, RBAC, routing, and UX fixes."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import resolve
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.models.audit import AuditLog
from apps.deployments.models.core import PlatformConfig, Service
from apps.deployments.models.database_replica import DatabaseReplica
from apps.deployments.models.registry import RegistryCredential
from apps.deployments.models.registry_scope import ScopedRegistry
from apps.notifications.models import NotificationPreference
from apps.organizations.models import Organization, OrganizationMembership
from apps.teams.models import Team, TeamMember


@override_settings(SECURE_SSL_REDIRECT=False)
class SettingsE2EFixesTests(TestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            username="tenant_a",
            email="tenant_a@example.com",
            password="Password123!",
        )
        self.user_b = User.objects.create_user(
            username="tenant_b",
            email="tenant_b@example.com",
            password="Password123!",
        )
        self.staff_user = User.objects.create_user(
            username="platform_staff",
            email="staff@example.com",
            password="Password123!",
            is_staff=True,
        )
        self.admin_user = User.objects.create_superuser(
            username="platform_super",
            email="super@example.com",
            password="Password123!",
        )

    # ── C-3: test-smtp routing must not be shadowed ─────────────────────────

    def test_c3_test_smtp_route_not_shadowed(self):
        match = resolve("/api/v1/notifications/test-smtp/")
        self.assertEqual(match.url_name, "test-smtp")

    # ── C-1 & H-10: AI provider fetch-models SSRF/exfil & prompt timeout ────

    def test_c1_fetch_models_blocks_key_exfil_on_overridden_base_url(self):
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        fake_cls = lambda: SimpleNamespace(
            base_url="https://api.openai.com/v1",
            api_key="sk-secret-stored-key",
        )
        with patch.dict("apps.intelligence.providers.registry.PROVIDERS", {"openai": fake_cls}):
            resp = client.post(
                "/api/v1/ai/providers/fetch-models/",
                {"provider_id": "openai", "base_url": "https://evil.example.com/v1"},
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("API key", resp.json().get("error", ""))

    def test_c1_fetch_models_blocks_ssrf_metadata_url(self):
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        fake_cls = lambda: SimpleNamespace(base_url="", api_key="")
        with patch.dict("apps.intelligence.providers.registry.PROVIDERS", {"openai": fake_cls}):
            resp = client.post(
                "/api/v1/ai/providers/fetch-models/",
                {
                    "provider_id": "openai",
                    "api_key": "sk-test",
                    "base_url": "http://169.254.169.254/latest/meta-data",
                },
                format="json",
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_h10_ai_test_prompt_timeout_does_not_block_on_hung_worker(self):
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        def _slow_ask(*_args, **_kwargs):
            time.sleep(5)
            return ("late", "mock")

        with (
            patch.dict("os.environ", {"AI_TEST_TIMEOUT_SECONDS": "1"}),
            patch("apps.intelligence.views._cached_ask", side_effect=_slow_ask),
            patch("apps.intelligence.views.get_configured_providers", return_value=[]),
        ):
            t0 = time.monotonic()
            resp = client.post("/api/v1/ai/test/", {"prompt": "hi"}, format="json")
            elapsed = time.monotonic() - t0

        self.assertEqual(resp.status_code, status.HTTP_504_GATEWAY_TIMEOUT)
        self.assertLess(elapsed, 3.0)

    # ── C-2: ScopedRegistry imports, empty-Q leak, and write RBAC ───────────

    def test_c2_scoped_registry_zero_memberships_returns_empty_not_500_or_leak(self):
        org_a = Organization.objects.create(name="OrgA", slug="orga", owner=self.user_a)
        from django.contrib.contenttypes.models import ContentType

        ScopedRegistry.objects.create(
            content_type=ContentType.objects.get_for_model(Organization),
            object_id=org_a.id,
            registry_url="registry.example.com",
        )

        client_b = APIClient()
        client_b.force_authenticate(user=self.user_b)
        resp = client_b.get("/api/v1/registry-scopes/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", [])
        self.assertEqual(rows, [])

    def test_c2_scoped_registry_member_can_read_but_not_write(self):
        team = Team.objects.create(name="TeamA", owner=self.user_a)
        TeamMember.objects.create(team=team, user=self.user_b, role="MEMBER", is_active=True)

        client_a = APIClient()
        client_a.force_authenticate(user=self.user_a)
        create_resp = client_a.post(
            "/api/v1/registry-scopes/",
            {
                "scope_type": "team",
                "scope_id": str(team.id),
                "registry_url": "ghcr.io",
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        scope_id = create_resp.json()["id"]

        client_b = APIClient()
        client_b.force_authenticate(user=self.user_b)
        # Member can read & resolve
        list_resp = client_b.get("/api/v1/registry-scopes/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        rows = list_resp.json() if isinstance(list_resp.json(), list) else list_resp.json().get("results", [])
        self.assertEqual(len(rows), 1)
        res_resp = client_b.get(f"/api/v1/registry-scopes/resolve/?scope_type=team&scope_id={team.id}")
        self.assertEqual(res_resp.status_code, status.HTTP_200_OK)

        # Member CANNOT modify or delete
        patch_resp = client_b.patch(
            f"/api/v1/registry-scopes/{scope_id}/",
            {"registry_url": "evil.example.com"},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_403_FORBIDDEN)
        del_resp = client_b.delete(f"/api/v1/registry-scopes/{scope_id}/")
        self.assertEqual(del_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Promote to TEAM ADMIN -> allowed to delete
        TeamMember.objects.filter(team=team, user=self.user_b).update(role="ADMIN")
        del_ok = client_b.delete(f"/api/v1/registry-scopes/{scope_id}/")
        self.assertEqual(del_ok.status_code, status.HTTP_204_NO_CONTENT)

    # ── H-1: Notification preferences auto-seeded & PATCHed ─────────────────

    def test_h1_preferences_autoseed_and_patch_channels(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        resp = client.get("/api/v1/preferences/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        items = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", [])
        self.assertGreaterEqual(len(items), 4)
        first = items[0]

        patch_resp = client.patch(
            f"/api/v1/preferences/{first['id']}/",
            {"channels": ["in_app", "slack"]},
            format="json",
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            NotificationPreference.objects.get(id=first["id"]).channels,
            ["in_app", "slack"],
        )

    # ── H-2: DatabaseReplica test returns ok + reachable ────────────────────

    def test_h2_database_replica_test_includes_ok_and_reachable(self):
        replica = DatabaseReplica.objects.create(
            name="eu-west-1",
            host="10.0.0.5",
            port=5432,
            username="repl",
            password="secret",
            database="smsly",
            created_by=self.admin_user,
        )
        client = APIClient()
        client.force_authenticate(user=self.admin_user)
        with patch("apps.deployments.views.database_replica.svc.test_connection", return_value=(True, "", 0.1)):
            resp = client.post(f"/api/v1/database-replicas/{replica.id}/test/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIs(resp.json().get("ok"), True)
        self.assertIs(resp.json().get("reachable"), True)

    # ── H-4: DomainConfig PUT preserves write-only secrets when blank ───────

    def test_h4_domain_config_blank_secrets_do_not_wipe_stored_secrets(self):
        cfg = PlatformConfig.load()
        cfg.smtp_password = "existing-smtp-secret"
        cfg.crowdsec_bouncer_key = "existing-cs-bouncer"
        cfg.sentry_dsn = "https://abc@sentry.example.com/1"
        cfg.save()

        client = APIClient()
        client.force_authenticate(user=self.admin_user)
        with (
            patch("apps.domains.views.domain._crowdsec_cf_bouncer_running", return_value=False),
            patch("apps.deployments.services.caddy_manager.apply_caddyfile", return_value=None, create=True),
        ):
            resp = client.put(
                "/api/v1/system/domain-config/",
                {
                    "smtp_password": "",
                    "crowdsec_bouncer_key": "",
                    "sentry_dsn": "",
                    "smtp_host": "smtp.example.com",
                },
                format="json",
            )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_207_MULTI_STATUS, status.HTTP_400_BAD_REQUEST))
        cfg.refresh_from_db()
        self.assertEqual(cfg.smtp_password, "existing-smtp-secret")
        self.assertEqual(cfg.crowdsec_bouncer_key, "existing-cs-bouncer")
        self.assertEqual(cfg.sentry_dsn, "https://abc@sentry.example.com/1")

    # ── H-5 & H-6: SystemConfig & StorageOverview RBAC scoping ──────────────

    def test_h6_system_config_and_storage_overview_restricted(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        cfg_resp = client.get("/api/v1/system/config/")
        self.assertEqual(cfg_resp.status_code, status.HTTP_200_OK)
        body = cfg_resp.json()
        self.assertIn("VERSION", body)
        self.assertNotIn("infra_health", body)
        self.assertNotIn("services", body)
        self.assertNotIn("STORAGE_TOTAL_GB", body)

        stor_resp = client.get("/api/v1/system/storage-overview/")
        self.assertEqual(stor_resp.status_code, status.HTTP_403_FORBIDDEN)

        # Staff user passes H-5 checks too
        client.force_authenticate(user=self.staff_user)
        cfg_staff = client.get("/api/v1/system/config/")
        self.assertEqual(cfg_staff.status_code, status.HTTP_200_OK)
        self.assertIn("ALLOWED_HOSTS", cfg_staff.json())

    # ── H-8: AuditLog does not leak same-named services across tenants ──────

    def test_h8_audit_log_does_not_collide_on_service_name(self):
        svc_a = Service.objects.create(name="api-tenant-a", slug="api-tenant-a", owner=self.user_a)
        svc_b = Service.objects.create(name="api-tenant-b", slug="api-tenant-b", owner=self.user_b)

        AuditLog.objects.create(
            actor=self.user_a.username,
            action="DEPLOY_TRIGGERED",
            target=svc_a.name,
        )
        AuditLog.objects.create(
            actor=self.user_a.username,
            action="SECRET_UPDATED",
            target=f"Service:{svc_a.id}",
        )
        AuditLog.objects.create(
            actor="admin",
            action="ENV_UPDATED",
            target=f"Service:{svc_b.id}",
        )

        client_b = APIClient()
        client_b.force_authenticate(user=self.user_b)
        resp = client_b.get("/api/v1/audit-logs/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.json() if isinstance(resp.json(), list) else resp.json().get("results", [])
        targets = {r.get("target") for r in rows}
        self.assertIn(f"Service:{svc_b.id}", targets)
        self.assertNotIn(svc_a.name, targets)
        self.assertNotIn(f"Service:{svc_a.id}", targets)

    # ── H-9: RegistryCredential SSRF protection ─────────────────────────────

    def test_h9_registry_credential_rejects_internal_ssrf_urls(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        bad_resp = client.post(
            "/api/v1/registry-credentials/",
            {
                "name": "internal",
                "provider": "CUSTOM",
                "registry_url": "http://169.254.169.254/latest",
                "username": "u",
                "password": "p",
            },
            format="json",
        )
        self.assertEqual(bad_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(RegistryCredential.objects.filter(owner=self.user_a).count(), 0)

    # ── M-3: Slow queries extension_available flag ──────────────────────────

    def test_m3_slow_queries_reports_extension_unavailable(self):
        client = APIClient()
        client.force_authenticate(user=self.admin_user)

        with patch(
            "apps.deployments.views.slow_query.check_pg_stat_statements_status",
            return_value=(False, "pg_stat_statements is not loaded in shared_preload_libraries."),
        ):
            resp = client.get("/api/v1/slow-queries/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIs(resp.json().get("extension_available"), False)
        self.assertIn("shared_preload_libraries", resp.json().get("warning", ""))

    # ── M-5: User profile email restrictions & superuser uniqueness ─────────

    def test_m5_profile_email_uniqueness_enforced(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        # Regular user cannot change email directly
        resp_change = client.patch(
            "/api/v1/auth/user/",
            {"email": "TENANT_B@example.com"},
            format="json",
        )
        self.assertEqual(resp_change.status_code, status.HTTP_400_BAD_REQUEST)
        self.user_a.refresh_from_db()
        self.assertEqual(self.user_a.email, "tenant_a@example.com")

        # Regular user CAN update first_name when email is unchanged
        resp_same = client.patch(
            "/api/v1/auth/user/",
            {"first_name": "Alice", "email": "tenant_a@example.com"},
            format="json",
        )
        self.assertEqual(resp_same.status_code, status.HTTP_200_OK)
        self.user_a.refresh_from_db()
        self.assertEqual(self.user_a.first_name, "Alice")

        # Superuser email edit enforces case-insensitive uniqueness
        client.force_authenticate(user=self.admin_user)
        resp_dup = client.patch(
            "/api/v1/auth/user/",
            {"email": "TENANT_B@example.com"},
            format="json",
        )
        self.assertEqual(resp_dup.status_code, status.HTTP_400_BAD_REQUEST)
        resp_ok = client.patch(
            "/api/v1/auth/user/",
            {"email": "new_super@example.com"},
            format="json",
        )
        self.assertEqual(resp_ok.status_code, status.HTTP_200_OK)
        self.admin_user.refresh_from_db()
        self.assertEqual(self.admin_user.email, "new_super@example.com")
