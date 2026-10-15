"""Sablier blocking wake strategy (no waiting page).

Regression tests (2026-10-11): wake used the dynamic waiting page,
which leaks container/group names and topology to the public. The
platform now uses the blocking strategy everywhere — the request
simply holds until the container is ready, then proxies normally.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.traefik_labels import sablier_service_labels
from apps.deployments.services.traefik_manager.edge_shared_file import (
    EDGE_SHARED_YML,
)


class SablierBlockingStrategyTests(SimpleTestCase):
    def test_blocking_timeout_set(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        key = ("traefik.http.middlewares.myapp-sablier.plugin.sablier."
               "blocking.timeout")
        self.assertIn(key, labels)
        self.assertTrue(labels[key].strip())

    def test_no_dynamic_block_anywhere(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        for key in labels:
            self.assertNotIn(".dynamic.", key,
                             f"waiting-page key leaked: {key}")
        for key, value in labels.items():
            if key.endswith(".group"):
                continue
            self.assertNotIn("my-app", str(value),
                             f"service name leaked in {key}")

    def test_group_still_targets_service(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        self.assertEqual(
            labels["traefik.http.middlewares.myapp-sablier.plugin.sablier.group"],
            "my-app")
        self.assertEqual(labels["sablier.group"], "my-app")
        self.assertEqual(labels["sablier.enable"], "true")
        self.assertIn("traefik.docker.allownonrunning", labels)

    def test_shared_default_is_blocking(self):
        self.assertIn("blocking:", EDGE_SHARED_YML)
        self.assertIn("timeout:", EDGE_SHARED_YML)
        self.assertNotIn("dynamic:", EDGE_SHARED_YML)
        self.assertNotIn("showDetails", EDGE_SHARED_YML)


class SablierWakeRetryTests(SimpleTestCase):
    def test_retry_labels_present(self):
        from apps.deployments.services.traefik_labels import (
            sablier_retry_labels,
            sablier_retry_ref,
        )
        labels = sablier_retry_labels(router_name="myapp")
        self.assertEqual(
            labels["traefik.http.middlewares.myapp-wakeretry.retry.attempts"],
            "5")
        self.assertEqual(
            labels["traefik.http.middlewares.myapp-wakeretry.retry.initialinterval"],
            "2s")
        self.assertEqual(
            labels["traefik.http.middlewares.myapp-wakeretry.retry.retryexpression"],
            "IsNetworkError() || ResponseCode() >= 500")
        self.assertNotIn(
            "traefik.http.middlewares.myapp-wakeretry.retry.status", labels)
        self.assertEqual(sablier_retry_ref("myapp"), "myapp-wakeretry")

    def test_generate_labels_chains_retry_before_sablier(self):
        from apps.deployments.services.traefik_labels import (
            generate_traefik_labels,
        )
        svc = mock.Mock()
        svc.sablier_enabled = True
        svc.edge_jwt_required = False
        svc.sablier_session = "10m"
        labels = generate_traefik_labels(
            service_name="my-app", domain="my-app.example.com",
            internal_port=8000, service=svc)
        chain = labels["traefik.http.routers.my_app.middlewares"]
        retry_at = chain.index("my_app-wakeretry")
        sab_at = chain.index("my_app-sablier")
        self.assertLess(retry_at, sab_at)
        self.assertIn(
            "traefik.http.middlewares.my_app-wakeretry.retry.attempts", labels)
