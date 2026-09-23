"""Retention hygiene: build-cache cap, registry tag expiry, rollback pins."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks.infra.tasks_maintenance import (
    _rollback_protected_tags,
    select_expired_registry_tags,
)


def _tag(repo, tag, age_days=None):
    created = None
    if age_days is not None:
        created = timezone.now() - timedelta(days=age_days)
    return {"tag": tag, "digest": "sha256:" + tag, "created": created}

class SelectExpiredRegistryTagsTests(SimpleTestCase):
    def test_old_unprotected_tag_expires(self):
        expired = select_expired_registry_tags(
            {"smsly/svc": [_tag("smsly/svc", "abc1234", 10), _tag("smsly/svc", "def5678", 1)]},
            timezone.now(), 7, set(),
        )
        self.assertEqual(expired, [("smsly/svc", "abc1234", "sha256:abc1234")])

    def test_last_tag_never_deleted(self):
        # A repo must never be left tagless by retention, even when its
        # only tag is old and unprotected (metadata glitch backstop).
        self.assertEqual(
            select_expired_registry_tags(
                {"smsly/svc": [_tag("smsly/svc", "abc1234", 30)]},
                timezone.now(), 7, set(),
            ),
            [],
        )

    def test_fresh_tag_kept(self):
        self.assertEqual(
            select_expired_registry_tags(
                {"smsly/svc": [_tag("smsly/svc", "abc1234", 2)]},
                timezone.now(), 7, set(),
            ),
            [],
        )

    def test_protected_tag_kept_despite_age(self):
        self.assertEqual(
            select_expired_registry_tags(
                {"smsly/svc": [_tag("smsly/svc", "abc1234", 30)]},
                timezone.now(), 7, {("smsly/svc", "abc1234")},
            ),
            [],
        )

    def test_unknown_age_never_deleted(self):
        self.assertEqual(
            select_expired_registry_tags(
                {"smsly/svc": [_tag("smsly/svc", "abc1234", None)]},
                timezone.now(), 7, set(),
            ),
            [],
        )

    def test_entries_without_tag_or_digest_skipped(self):
        repo_tags = {"smsly/svc": [
            {"tag": "", "digest": "sha256:x", "created": timezone.now() - timedelta(days=30)},
            {"tag": "abc1234", "digest": "", "created": timezone.now() - timedelta(days=30)},
        ]}
        self.assertEqual(
            select_expired_registry_tags(repo_tags, timezone.now(), 7, set()), [])


class RollbackProtectedTagsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retention-user", password="x")
        self.provider = CloudProvider.objects.create(
            name="local-retention",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="retention-svc",
            owner=self.user,
            provider=self.provider,
        )

    def _make_dep(self, commit, status, age_days):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash=commit,
            status=status,
        )
        Deployment.objects.filter(pk=dep.pk).update(
            created_at=timezone.now() - timedelta(days=age_days))
        return dep

    def test_retain_newest_active_plus_inflight(self):
        self._make_dep("a" * 40, Deployment.Status.ACTIVE, 30)
        self._make_dep("b" * 40, Deployment.Status.ACTIVE, 20)
        self._make_dep("c" * 40, Deployment.Status.ACTIVE, 10)
        self._make_dep("d" * 40, Deployment.Status.FAILED, 40)
        self._make_dep("e" * 40, Deployment.Status.QUEUED, 1)

        protected = _rollback_protected_tags(retain=2)
        repo = "smsly/retention-svc"
        self.assertIn((repo, "c" * 7), protected)
        self.assertIn((repo, "b" * 7), protected)
        self.assertNotIn((repo, "a" * 7), protected)
        self.assertNotIn((repo, "d" * 7), protected)
        self.assertIn((repo, "e" * 7), protected)

    def test_db_failure_returns_none_fail_closed(self):
        # Callers must abort deletion when protections are unknown —
        # an empty set would look like "nothing to protect".
        stub = MagicMock()
        stub.objects.select_related.side_effect = RuntimeError("db down")
        with patch("apps.deployments.models.Deployment", stub):
            self.assertIsNone(_rollback_protected_tags(retain=2))


class PruneStaleBuildCacheTests(SimpleTestCase):
    @patch("apps.deployments.services.builders.subprocess.run")
    def test_uses_24h_cap_by_default(self, mock_run):
        from apps.deployments.services.builders import prune_stale_build_cache

        prune_stale_build_cache()
        args = mock_run.call_args[0][0]
        self.assertIn("until=24h", args)

    @patch("apps.deployments.services.builders.subprocess.run")
    def test_env_override(self, mock_run):
        from apps.deployments.services.builders import prune_stale_build_cache

        with patch.dict("os.environ", {"BUILD_CACHE_MAX_AGE_HOURS": "48"}):
            prune_stale_build_cache()
        args = mock_run.call_args[0][0]
        self.assertIn("until=48h", args)

    @patch("apps.deployments.services.builders.subprocess.run")
    def test_failure_never_raises(self, mock_run):
        from apps.deployments.services.builders import prune_stale_build_cache

        mock_run.side_effect = RuntimeError("docker down")
        prune_stale_build_cache()


class CleanupBuildCacheTaskTests(SimpleTestCase):
    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_prunes_with_24h_cap(self, mock_client_fn):
        from apps.core.tasks.metrics import cleanup_build_cache_task

        client = MagicMock()
        client.api.prune_builds.return_value = {"SpaceReclaimed": 0}
        mock_client_fn.return_value = client
        cleanup_build_cache_task()
        client.api.prune_builds.assert_called_once_with(filters={"until": "24h"})

    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_prune_all_drops_age_filter_and_reports_mb(self, mock_client_fn):
        from apps.core.tasks.metrics import cleanup_build_cache_task

        client = MagicMock()
        client.api.prune_builds.return_value = {"SpaceReclaimed": 3 * 1024 * 1024}
        mock_client_fn.return_value = client
        outcome = cleanup_build_cache_task(prune_all=True)
        client.api.prune_builds.assert_called_once_with(all=True)
        self.assertEqual(outcome, {"reclaimed_mb": 3})

    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_no_client_returns_zero(self, mock_client_fn):
        from apps.core.tasks.metrics import cleanup_build_cache_task

        mock_client_fn.return_value = None
        self.assertEqual(cleanup_build_cache_task(prune_all=True),
                         {"reclaimed_mb": 0})


class BuildImageRefreshHookTests(SimpleTestCase):
    @patch("apps.deployments.services.builders.prune_stale_build_cache")
    def test_successful_build_refreshes_cache(self, mock_prune):
        from apps.deployments.services.builders import BuildManager

        service = MagicMock()
        service.name = "hook-svc"
        service.repository_url = ""
        service.branch = "main"
        service.buildpack = "DOCKER"
        service.root_directory = "./"
        service.build_command = ""
        deployment = MagicMock()
        deployment.id = "dep-hook-1"
        deployment.service = service
        deployment.commit_hash = "abc1234"

        manager = BuildManager(deployment)
        manager._log = MagicMock()
        manager._run_command = MagicMock()
        manager._run_security_scan = MagicMock()
        manager._get_github_access_token = MagicMock(return_value="")
        # Bypass the real /tmp work dir (plain attribute, no FS at init).
        manager.work_dir = MagicMock()
        manager.work_dir.exists.return_value = False

        tag = manager.build_image()

        self.assertTrue(tag.endswith(":abc1234"))
        mock_prune.assert_called_once()


class PullOnMissTests(SimpleTestCase):
    def _step(self, image_name="smsly/hook-svc:abc1234"):
        from apps.deployments.services.pipeline.build import BuildMixin

        step = BuildMixin.__new__(BuildMixin)
        step.image_name = image_name
        step.deployment = MagicMock()
        return step

    def test_pull_success_retags_and_returns_true(self):
        step = self._step()
        pulled = MagicMock()
        client = MagicMock()
        client.images.pull.return_value = pulled
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client), patch(
            "django.conf.settings.CONTAINER_REGISTRY_URL", "registry:5000", create=True
        ):
            self.assertTrue(step._pull_cached_image())
        client.images.pull.assert_called_once_with("registry:5000/smsly/hook-svc:abc1234")
        pulled.tag.assert_called_once_with("smsly/hook-svc", "abc1234")

    def test_pull_failure_returns_false(self):
        step = self._step()
        client = MagicMock()
        client.images.pull.side_effect = RuntimeError("denied")
        with patch("apps.cloud.docker_client.get_docker_client", return_value=client):
            self.assertFalse(step._pull_cached_image())

    def test_missing_registry_config_returns_false(self):
        step = self._step()
        with patch("django.conf.settings.CONTAINER_REGISTRY_URL", "", create=True):
            self.assertFalse(step._pull_cached_image())


class RegistrySessionTests(SimpleTestCase):
    def _session(self, outcomes):
        session = MagicMock()
        calls = {"n": 0}

        def _get(url, **kwargs):
            idx = min(calls["n"], len(outcomes) - 1)
            calls["n"] += 1
            code, exc = outcomes[idx]
            if exc is not None:
                raise exc
            resp = MagicMock()
            resp.status_code = code
            return resp

        session.get.side_effect = _get
        return session

    def test_prefers_verified_https(self):
        from apps.deployments.tasks.infra import tasks_maintenance as tm

        session = self._session([(200, None)])
        with patch("requests.Session", return_value=session), patch(
            "django.conf.settings.CONTAINER_REGISTRY_URL", "registry:5000", create=True
        ), patch("django.conf.settings.REGISTRY_USER", "", create=True), patch(
            "django.conf.settings.REGISTRY_PASSWORD", "", create=True
        ):
            _, base = tm._registry_session()
        self.assertEqual(base, "https://registry:5000")

    def test_falls_back_to_http_when_https_unreachable(self):
        import requests

        from apps.deployments.tasks.infra import tasks_maintenance as tm

        session = self._session([
            (0, requests.ConnectionError("down")),
            (0, requests.ConnectionError("down")),
        ])
        with patch("requests.Session", return_value=session), patch(
            "django.conf.settings.CONTAINER_REGISTRY_URL", "registry:5000", create=True
        ), patch("django.conf.settings.REGISTRY_USER", "", create=True), patch(
            "django.conf.settings.REGISTRY_PASSWORD", "", create=True
        ):
            _, base = tm._registry_session()
        self.assertEqual(base, "http://registry:5000")
