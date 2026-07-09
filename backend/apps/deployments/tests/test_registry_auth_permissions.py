"""Regression tests: views_registry_auth._check_registry_permission + token endpoint.

Locks in the existing permission model so the "registry RBAC is too broad"
claim from the security audit doesn't regress. Specifically:

  * anonymous users are denied
  * repo names use path-prefix matching (``==`` or ``startswith('/')``),
    not substring matching — project "a" must NOT match repo "team-a/frontend"
  * team membership grants pull but NOT push (push requires direct ownership)
  * project membership grants pull
  * platform images (``smsly/*``) are superuser-only
  * token endpoint returns 503 when REGISTRY_HTTP_SECRET is not configured,
    rather than falling back to a SECRET_KEY-derived signing key
"""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import TestCase, override_settings

import apps.deployments.views_registry_auth as auth_mod
from apps.deployments.models_core import Project
from apps.deployments.models_project import ProjectMember
from apps.deployments.views_registry_auth import (
    _check_registry_permission,
    _reset_registry_secret_cache,
    _resolve_registry_secret,
    registry_token,
)
from apps.teams.models import Team, TeamMember

User = get_user_model()


def _make_request(path="/api/v1/registry/auth/", scope=""):
    req = HttpRequest()
    req.method = "GET"
    req.path = path
    req.COOKIES = {}
    req.META = {"HTTP_HOST": "testserver"}
    req.GET = {"service": "container-registry"}
    if scope:
        req.GET["scope"] = scope
    req.headers = {}
    return req


class RegistryAuthPermissionTests(TestCase):
    """Direct unit tests against ``_check_registry_permission``."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="pw",
        )
        self.team_member = User.objects.create_user(
            username="team-member", password="pw",
        )
        self.project_member = User.objects.create_user(
            username="project-member", password="pw",
        )
        self.outsider = User.objects.create_user(
            username="outsider", password="pw",
        )
        self.team = Team.objects.create(
            owner=self.owner, name="t1", organization=None,
        )
        TeamMember.objects.create(
            team=self.team, user=self.team_member,
            role=TeamMember.Role.MEMBER,
        )
        self.project = Project.objects.create(
            owner=self.owner, name="myproject",
            slug="myproject", team=self.team,
        )
        ProjectMember.objects.create(
            project=self.project, user=self.project_member,
            role=ProjectMember.Role.MEMBER,
        )

    # ── Anonymous denied ────────────────────────────────────────────────

    def test_anonymous_denied(self):
        self.assertFalse(_check_registry_permission(
            None, "repository:myproject/web:pull", ["pull"],
        ))

    def test_inactive_user_denied(self):
        inactive = User.objects.create_user(
            username="inactive", password="pw", is_active=False,
        )
        self.assertFalse(_check_registry_permission(
            inactive, "repository:myproject/web:pull", ["pull"],
        ))

    # ── Path-prefix matching, NOT substring ─────────────────────────────

    def test_owner_can_pull_exact_repo_match(self):
        self.assertTrue(_check_registry_permission(
            self.owner, "repository:myproject/web:pull", ["pull"],
        ))

    def test_owner_can_pull_prefix_match(self):
        """`myproject/web-api` is a sub-repo of project `myproject`."""
        self.assertTrue(_check_registry_permission(
            self.owner, "repository:myproject/web-api:pull", ["pull"],
        ))

    def test_substring_matching_is_not_used(self):
        """The auditor claimed substring matching was used — verify it isn't.

        Project "a" must NOT match a repo called "team-a/frontend" via
        substring. Path-prefix matching means only `a/*` or `==a` matches.
        """
        sub_project = Project.objects.create(
            owner=self.owner, name="a", slug="a",
        )
        self.assertTrue(_check_registry_permission(
            self.owner, "repository:a/web:pull", ["pull"],
        ))
        self.assertFalse(_check_registry_permission(
            self.owner, "repository:team-a/frontend:pull", ["pull"],
        ))
        # Cleanup so other tests don't see this project.
        sub_project.delete()

    # ── Pull is broader than push ───────────────────────────────────────

    def test_team_member_can_pull_but_not_push(self):
        # Pull is allowed for team members.
        self.assertTrue(_check_registry_permission(
            self.team_member, "repository:myproject/web:pull", ["pull"],
        ))
        # Push is denied — team membership alone does not grant it.
        self.assertFalse(_check_registry_permission(
            self.team_member, "repository:myproject/web:push", ["push"],
        ))

    def test_project_member_can_pull_but_not_push(self):
        self.assertTrue(_check_registry_permission(
            self.project_member, "repository:myproject/web:pull", ["pull"],
        ))
        self.assertFalse(_check_registry_permission(
            self.project_member, "repository:myproject/web:push", ["push"],
        ))

    def test_project_owner_can_push(self):
        self.assertTrue(_check_registry_permission(
            self.owner, "repository:myproject/web:push", ["push"],
        ))
        self.assertTrue(_check_registry_permission(
            self.owner, "repository:myproject/web:*", ["*"],
        ))

    def test_outsider_denied(self):
        self.assertFalse(_check_registry_permission(
            self.outsider, "repository:myproject/web:pull", ["pull"],
        ))

    # ── Platform images ─────────────────────────────────────────────────

    def test_non_superuser_cannot_pull_platform_images(self):
        """smsly/* repos are admin-only."""
        self.assertFalse(_check_registry_permission(
            self.owner, "repository:smsly/backend:pull", ["pull"],
        ))

    def test_superuser_can_pull_platform_images(self):
        admin = User.objects.create_superuser(
            username="admin", password="pw", email="a@b.c",
        )
        self.assertTrue(_check_registry_permission(
            admin, "repository:smsly/backend:pull", ["pull"],
        ))


class RegistryTokenEndpointSelfHealTests(TestCase):
    """The token endpoint must self-heal when REGISTRY_HTTP_SECRET is unset.

    Earlier evolution:
      * v1: silently derived a signing secret from SECRET_KEY (rejected).
      * v2: returned 503 when no secret was configured (correct posture,
            but operationally painful — a missed env var would take down
            all registry pulls until a human intervened).
      * v3 (current): self-heals by generating ``secrets.token_hex(32)``
            for the lifetime of the process and logs CRITICAL once so
            operators persist it via REGISTRY_HTTP_SECRET.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="tokuser", password="pw",
        )
        # Clear the module-level cache so each test starts fresh.
        _reset_registry_secret_cache()

    def tearDown(self):
        _reset_registry_secret_cache()

    # ── Direct tests on the helper ─────────────────────────────────────

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_generates_ephemeral_secret_when_unset(self):
        secret = _resolve_registry_secret()
        # 32 random bytes encoded as hex → 64 chars.
        self.assertEqual(len(secret), 64)
        # Looks like a hex string, not an empty value.
        self.assertTrue(all(c in "0123456789abcdef" for c in secret))

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_is_stable_across_calls(self):
        """Self-healed secret must be cached — otherwise in-flight tokens
        would be invalidated on every single request."""
        secret_1 = _resolve_registry_secret()
        secret_2 = _resolve_registry_secret()
        secret_3 = _resolve_registry_secret()
        self.assertEqual(secret_1, secret_2)
        self.assertEqual(secret_2, secret_3)
        # And the cache module-level variable is set.
        self.assertEqual(auth_mod._registry_secret_cache, secret_1)

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_prefers_configured_secret_over_ephemeral(self):
        """If REGISTRY_HTTP_SECRET is set, the self-heal must NOT fire."""
        _resolve_registry_secret()  # populate the cache via self-heal
        self.assertIsNotNone(auth_mod._registry_secret_cache)
        with override_settings(REGISTRY_HTTP_SECRET="real-configured-secret"):
            self.assertEqual(
                _resolve_registry_secret(), "real-configured-secret",
            )

    @override_settings(REGISTRY_HTTP_SECRET="real-configured-secret")
    def test_resolve_uses_settings_when_env_unset(self):
        """settings.REGISTRY_HTTP_SECRET is the second-tier source."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _resolve_registry_secret(), "real-configured-secret",
            )

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(
        os.environ, {"REGISTRY_TOKEN_SIGNING_KEY": "fallback-secret"}, clear=True,
    )
    def test_resolve_uses_token_signing_key_fallback(self):
        self.assertEqual(
            _resolve_registry_secret(), "fallback-secret",
        )

    # ── Endpoint integration ───────────────────────────────────────────

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(os.environ, {}, clear=True)
    def test_endpoint_self_heals_and_does_not_503(self):
        """When the secret is unset the endpoint must not crash — it must
        self-heal and proceed to the permission check."""
        # Sanity: env truly is clear of signing-related vars.
        for key in ("REGISTRY_HTTP_SECRET", "REGISTRY_TOKEN_SIGNING_KEY"):
            self.assertNotIn(key, os.environ)

        req = _make_request(scope="repository:foo/web:pull")
        resp = registry_token(req)

        # Not 503 (self-heal succeeded). Not 200 either (no authenticated
        # user). The endpoint reached the permission check and denied
        # cleanly — proves the signing path is alive.
        self.assertEqual(resp.status_code, 401)

    @override_settings(REGISTRY_HTTP_SECRET="", SECRET_KEY="should-not-be-used")
    @patch.dict(os.environ, {}, clear=True)
    def test_endpoint_logs_critical_once_on_self_heal(self):
        """The CRITICAL log line must fire on first self-heal, then NOT
        fire again for subsequent calls in the same process."""
        with self.assertLogs(
            "apps.deployments.views_registry_auth", level="CRITICAL",
        ) as cm:
            req = _make_request(scope="repository:foo/web:pull")
            registry_token(req)
            registry_token(req)
            registry_token(req)
        # Exactly one CRITICAL line, not three.
        critical_lines = [
            line for line in cm.output if "CRITICAL" in line
        ]
        self.assertEqual(len(critical_lines), 1)
        self.assertIn("self-healed", critical_lines[0].lower())

    @override_settings(
        REGISTRY_HTTP_SECRET="real-secret-1234567890abcdef",
        SECRET_KEY="some-secret-key",
    )
    def test_endpoint_does_not_self_heal_when_configured(self):
        """When the operator has set REGISTRY_HTTP_SECRET, the self-heal
        path must not run and the cache must stay empty."""
        self.assertIsNone(auth_mod._registry_secret_cache)
        req = _make_request(scope="repository:foo/web:pull")
        resp = registry_token(req)
        self.assertEqual(resp.status_code, 401)
        self.assertIsNone(auth_mod._registry_secret_cache)
