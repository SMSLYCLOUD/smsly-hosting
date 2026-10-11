"""Sablier wake-page privacy + wake-path routing.

Regression tests (2026-10-11): the waiting page used the service name
as displayName with showDetails=true, leaking container/group names
and topology to the public; node plain-:80 blocks bypassed Traefik so
sleeping services 503'd instead of waking.
"""
from django.test import SimpleTestCase

from apps.deployments.services.traefik_labels import sablier_service_labels
from apps.deployments.services.traefik_manager.edge_shared_file import (
    EDGE_SHARED_YML,
)


class SablierWakePagePrivacyTests(SimpleTestCase):
    def test_display_name_is_generic(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        key = "traefik.http.middlewares.myapp-sablier.plugin.sablier.dynamic.displayName"
        self.assertIn(key, labels)
        self.assertNotIn("my-app", labels[key])

    def test_details_hidden_neutral_theme(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        base = "traefik.http.middlewares.myapp-sablier.plugin.sablier.dynamic"
        self.assertEqual(labels[f"{base}.showDetails"], "false")
        self.assertEqual(labels[f"{base}.theme"], "ghost")

    def test_group_still_targets_service(self):
        labels = sablier_service_labels(
            router_name="myapp", group="my-app", session="10m")
        self.assertEqual(
            labels["traefik.http.middlewares.myapp-sablier.plugin.sablier.group"],
            "my-app")
        self.assertEqual(labels["sablier.group"], "my-app")
        self.assertEqual(labels["sablier.enable"], "true")

    def test_shared_default_hides_details(self):
        self.assertIn("showDetails: false", EDGE_SHARED_YML)
        self.assertNotIn("showDetails: true", EDGE_SHARED_YML)
        self.assertIn('theme: "ghost"', EDGE_SHARED_YML)
