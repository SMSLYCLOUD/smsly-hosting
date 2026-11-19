"""Credential restrictions must survive real DRF bearer authentication."""
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.core.models.api_token import APIToken, APITokenAuthentication
from apps.core.models import APIKey
from apps.core.views import APIKeyViewSet
from apps.deployments.views import _2fa
from apps.core.views import recovery
from apps.core.services.recovery import verify_recovery_phrase
from apps.deployments.models.core import PlatformConfig
from apps.core.views.tokens import create_token, list_tokens, revoke_token
from apps.mcp.views import McpTokenListView, McpTokenDetailView, McpToolCallView


@api_view(["GET", "POST"])
@authentication_classes([APITokenAuthentication, SessionAuthentication])
@permission_classes([IsAuthenticated])
def ordinary_resource(request):
    return Response({"access": "granted"})


urlpatterns = [
    path("tokens/create/", create_token),
    path("tokens/", list_tokens),
    path("tokens/<uuid:token_id>/", revoke_token),
    path("mcp/tokens/", McpTokenListView.as_view()),
    path("mcp/tokens/<str:token_id>/", McpTokenDetailView.as_view()),
    path("mcp/tools/<str:name>/call/", McpToolCallView.as_view()),
    path("resource/", ordinary_resource),
    path("recovery/generate/", recovery.recovery_phrase_generate),
    path("recovery/verify/", recovery.recovery_phrase_verify),
    path("mfa/enable/", _2fa.two_factor_enable),
    path("mfa/confirm/", _2fa.two_factor_confirm),
    path("mfa/disable/", _2fa.two_factor_disable),
    path("mfa/backup/", _2fa.two_factor_backup_codes),
    path("api-keys/", APIKeyViewSet.as_view({"get": "list", "post": "create"})),
    path("api-keys/<str:pk>/", APIKeyViewSet.as_view({"delete": "destroy"})),
]


@override_settings(ROOT_URLCONF=__name__,
                   MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware",
                               "django.contrib.auth.middleware.AuthenticationMiddleware"],
                   CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TokenScopeReadinessTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="scope-readiness")
        self.client = APIClient()

    def bearer(self, scopes=None, projects=None):
        token, raw = APIToken.create_token(self.user, scopes=scopes, projects=projects)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return token

    def test_restricted_tokens_cannot_mint_through_either_api(self):
        for scopes, projects in [(["read"], []), (["write"], []),
                                 (["read", "write"], []), ([], [str(uuid.uuid4())])]:
            self.bearer(scopes, projects)
            for endpoint in ["/tokens/create/", "/mcp/tokens/"]:
                before = APIToken.objects.count()
                with self.subTest(scopes=scopes, projects=projects, endpoint=endpoint):
                    response = self.client.post(endpoint, {"name": "escalated"}, format="json")
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(APIToken.objects.count(), before)

    def test_restricted_tokens_cannot_list_or_revoke_sibling_credentials(self):
        sibling, _ = APIToken.create_token(self.user)
        self.bearer(["read", "write"])
        for endpoint in ["/tokens/", "/mcp/tokens/"]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(endpoint).status_code, 403)
        for endpoint in [f"/tokens/{sibling.id}/", f"/mcp/tokens/{sibling.id}/"]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.delete(endpoint).status_code, 403)
                sibling.refresh_from_db()
                self.assertTrue(sibling.is_active)

    def test_read_scope_cannot_write_ordinary_rest(self):
        self.bearer(["read"])
        self.assertEqual(self.client.get("/resource/").status_code, 200)
        self.assertEqual(self.client.post("/resource/", {}, format="json").status_code, 403)

    def test_write_scope_does_not_imply_read(self):
        self.bearer(["write"])
        self.assertEqual(self.client.get("/resource/").status_code, 403)
        self.assertEqual(self.client.post("/resource/", {}, format="json").status_code, 200)

    def test_project_bound_token_denied_rest_without_object_policy(self):
        self.bearer(projects=[str(uuid.uuid4())])
        self.assertEqual(self.client.get("/resource/").status_code, 403)
        self.assertEqual(self.client.post("/resource/", {}, format="json").status_code, 403)

    def test_read_only_mcp_call_uses_tool_scope_not_post_method(self):
        self.bearer(["read"])
        response = self.client.post("/mcp/tools/list_projects/call/", {"args": {}}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"], [])
        response = self.client.post("/mcp/tools/cancel_deployment/call/", {"args": {}}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_project_bound_mcp_call_cannot_read_global_resources(self):
        self.bearer(projects=[str(uuid.uuid4())])
        response = self.client.post("/mcp/tools/list_services/call/", {"args": {}}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_unrestricted_token_can_mint_and_child_authenticates(self):
        self.bearer()
        for endpoint in ["/tokens/create/", "/mcp/tokens/"]:
            response = self.client.post(endpoint, {"name": "child"}, format="json")
            self.assertEqual(response.status_code, 201)
            child, _ = APIToken.verify(response.data["token"])
            self.assertEqual(child.pk, self.user.pk)

    def test_restricted_bearer_cannot_manage_ai_api_keys(self):
        key = APIKey.objects.create(user=self.user, name="existing", prefix="sk_dummy", key_hash="dummy")
        self.bearer(["read", "write"])
        before = APIKey.objects.count()
        self.assertEqual(self.client.post("/api-keys/", {"name": "escalated"}, format="json").status_code, 403)
        self.assertEqual(APIKey.objects.count(), before)
        self.assertEqual(self.client.get("/api-keys/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api-keys/{key.pk}/").status_code, 403)
        self.assertTrue(APIKey.objects.filter(pk=key.pk).exists())

    def test_unrestricted_bearer_can_create_ai_api_key(self):
        self.bearer()
        response = self.client.post("/api-keys/", {"name": "permitted"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["key"].startswith("sk_"))
        self.assertEqual(APIKey.objects.get(pk=response.data["id"]).user_id, self.user.pk)

    def test_restricted_bearers_cannot_manage_mfa_credentials(self):
        from django_otp.plugins.otp_static.models import StaticDevice
        from django_otp.plugins.otp_totp.models import TOTPDevice
        for scopes in [["read"], ["write"], ["read", "write"]]:
            self.bearer(scopes)
            with self.subTest(scopes=scopes):
                self.assertEqual(self.client.get("/mfa/backup/").status_code, 403)
                for endpoint in ["enable", "confirm", "disable"]:
                    self.assertEqual(self.client.post(f"/mfa/{endpoint}/", {}, format="json").status_code, 403)
                self.assertFalse(StaticDevice.objects.filter(user=self.user).exists())
                self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())

    def test_full_account_session_can_generate_mfa_backup_codes(self):
        from django_otp.plugins.otp_static.models import StaticToken
        self.client.force_login(self.user)
        # Match the browser session authentication used by these views.
        with self.settings(REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": [
                "rest_framework.authentication.SessionAuthentication"]}):
            original = _2fa.two_factor_backup_codes.cls.authentication_classes
            _2fa.two_factor_backup_codes.cls.authentication_classes = [SessionAuthentication]
            try:
                response = self.client.get("/mfa/backup/")
            finally:
                _2fa.two_factor_backup_codes.cls.authentication_classes = original
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["codes"]), 10)
        self.assertEqual(StaticToken.objects.filter(device__user=self.user).count(), 10)

    def test_restricted_bearers_cannot_replace_recovery_phrase(self):
        config = PlatformConfig.load()
        config.recovery_phrase_hash = "existing-recovery-credential"
        config.save(update_fields=["recovery_phrase_hash"])
        for scopes, projects in [(["read"], []), (["write"], []),
                                 (["read", "write"], []), ([], [str(uuid.uuid4())])]:
            self.bearer(scopes, projects)
            with self.subTest(scopes=scopes, projects=projects):
                response = self.client.post("/recovery/generate/", {}, format="json")
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("phrase", response.data)
                config.refresh_from_db()
                self.assertEqual(config.recovery_phrase_hash, "existing-recovery-credential")

    def assert_recovery_phrase_generated(self):
        response = self.client.post("/recovery/generate/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        words = response.data["phrase"].split()
        self.assertEqual(len(words), 12)
        stored = json.loads(PlatformConfig.load().recovery_phrase_hash)
        self.assertTrue(verify_recovery_phrase(words, stored["hash"], stored["salt"]))

    def test_recovery_phrase_generate_rejects_get(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.bearer()
        response = self.client.get("/recovery/generate/")
        self.assertEqual(response.status_code, 405)

    def test_unrestricted_non_admin_bearer_cannot_generate_recovery_phrase(self):
        config = PlatformConfig.load()
        config.recovery_phrase_hash = "existing-recovery-credential"
        config.save(update_fields=["recovery_phrase_hash"])
        self.bearer()
        response = self.client.post("/recovery/generate/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        config.refresh_from_db()
        self.assertEqual(config.recovery_phrase_hash, "existing-recovery-credential")

    def test_superuser_bearer_can_generate_recovery_phrase(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.bearer()
        self.assert_recovery_phrase_generated()

    def test_inactive_superuser_cannot_generate_recovery_phrase(self):
        self.user.is_superuser = True
        self.user.is_active = False
        self.user.save(update_fields=["is_superuser", "is_active"])
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/recovery/generate/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_non_admin_session_cannot_generate_recovery_phrase(self):
        config = PlatformConfig.load()
        config.recovery_phrase_hash = "existing-recovery-credential"
        config.save(update_fields=["recovery_phrase_hash"])
        self.client.force_login(self.user)
        with patch.object(recovery.recovery_phrase_generate.cls, "authentication_classes",
                          [APITokenAuthentication, SessionAuthentication]):
            response = self.client.post("/recovery/generate/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        config.refresh_from_db()
        self.assertEqual(config.recovery_phrase_hash, "existing-recovery-credential")

    def test_admin_session_can_generate_recovery_phrase(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)
        with patch.object(recovery.recovery_phrase_generate.cls, "authentication_classes",
                          [APITokenAuthentication, SessionAuthentication]):
            self.assert_recovery_phrase_generated()

    def test_anonymous_recovery_verification_remains_accessible(self):
        response = self.client.post("/recovery/verify/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Phrase and username are required")

    def test_recovery_verification_rejects_inactive_superuser(self):
        self.user.is_superuser = True
        self.user.is_active = False
        self.user.save(update_fields=["is_superuser", "is_active"])
        config = PlatformConfig.load()
        config.recovery_phrase_hash = json.dumps({"hash": "invalid", "salt": "salt"})
        config.save(update_fields=["recovery_phrase_hash"])
        with patch.object(recovery, "verify_recovery_phrase", return_value=True):
            response = self.client.post("/recovery/verify/",
                                        {"phrase": "one two three four five six seven eight nine ten eleven twelve",
                                         "username": self.user.username}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.user.is_active)
        self.assertIn(self.client.post("/recovery/generate/", {}, format="json").status_code, [401, 403])

    def test_recovery_verification_lockout_ignores_spoofed_xff(self):
        from django.core.cache import cache

        cache.clear()
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        config = PlatformConfig.load()
        config.recovery_phrase_hash = json.dumps({"hash": "invalid", "salt": "salt"})
        config.save(update_fields=["recovery_phrase_hash"])

        payload = {
            "phrase": "one two three four five six seven eight nine ten eleven twelve",
            "username": self.user.username,
        }
        # Bypass DRF's 5/hour throttle to test the 10-attempt atomic lockout + TrustedIdentMixin
        with patch.object(recovery.RecoveryPhraseThrottle, "allow_request", return_value=True), \
                patch.object(recovery, "verify_recovery_phrase", return_value=False):
            for idx in range(10):
                resp = self.client.post(
                    "/recovery/verify/",
                    payload,
                    format="json",
                    REMOTE_ADDR="198.51.100.42",
                    HTTP_X_FORWARDED_FOR=f"203.0.113.{idx + 1}",
                )
                self.assertEqual(resp.status_code, 403)

            locked = self.client.post(
                "/recovery/verify/",
                payload,
                format="json",
                REMOTE_ADDR="198.51.100.42",
                HTTP_X_FORWARDED_FOR="203.0.113.250",
            )
            self.assertEqual(locked.status_code, 429)

    def test_real_session_retains_full_capability(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post("/resource/", {}, format="json").status_code, 200)
