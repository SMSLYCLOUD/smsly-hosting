"""Sablier blocking wake strategy (no waiting page).

Regression tests (2026-10-11): wake used the dynamic waiting page,
which leaks container/group names and topology to the public. The
platform now uses the blocking strategy everywhere — the request
simply holds until the container is ready, then proxies normally.
"""
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
